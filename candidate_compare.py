"""Transparent candidate comparisons using dated, explicitly scoped artifacts."""
from __future__ import annotations

import copy

import pandas as pd

from data_freshness import norm_date
from decision_workbench import candidate_rows, number, snapshot_date, symbol_id
from market_insights import price_lookup, sessions
from strategy_playbooks import PLAYBOOKS


def dated_rows(frame, asof):
    if frame is None or frame.empty or snapshot_date(frame) != asof or "ts_code" not in frame:
        return {}
    clean = frame.where(pd.notna(frame), None)
    # Conflicting duplicates must not silently pick a price.
    clean = clean.drop_duplicates()
    clean = clean[~clean.duplicated("ts_code", keep=False)]
    return {symbol_id(r["ts_code"]): r for r in clean.to_dict("records") if symbol_id(r["ts_code"])}


def raw_quotes(frames, snapshot, asof):
    quotes = {}
    for frame, field, label in ((frames.get("etf"), "price_now", "ETF收盘榜"),
                                (frames.get("strong"), "price_now", "个股收盘榜"),
                                (frames.get("breakout"), "close", "新高收盘榜"),
                                (snapshot, "close", "全市场收盘快照")):
        for code, row in dated_rows(frame, asof).items():
            close = number(row.get(field))
            if close is not None and close > 0:
                quotes[code] = dict(close=close, date=asof, adjusted=False, source=label,
                                    name=row.get("name") or code,
                                    industry=row.get("industry") or row.get("细分行业") or "")
    return quotes


def archive(frames, evening, *, asof):
    result = dict(schema=1, date=asof, styles={})
    for style in PLAYBOOKS:
        if style == "fundamental":
            continue
        rows, excluded = candidate_rows(style, frames, evening, asof=asof)
        result["styles"][style] = dict(complete=not excluded, missing=excluded, rows=rows)
    return result


def membership(code, style, history, previous):
    document = history.get(previous) or {}
    block = document.get("styles", {}).get(style) or {}
    if norm_date(document.get("date")) != previous or block.get("complete") is not True:
        return "历史待积累"
    return "延续入选" if any(r.get("symbol") == code for r in block.get("rows", [])) else "较上一交易日新增"


def enrich(rows, frames, snapshot, analysis, history, asof):
    result = []
    days = sessions(analysis.get("calendar", {}), asof, 21)
    lookup = price_lookup(analysis.get("prices"), asof)
    quotes = raw_quotes(frames, snapshot, asof)
    indexes = {key: dated_rows(frame, asof) for key, frame in frames.items()}
    previous = days[-2] if len(days) >= 2 else ""
    for item in rows:
        row = copy.deepcopy(item)
        code = row["symbol"]
        meta = {}
        for key in ("etf", "breakout", "strong"):
            meta.update({k: v for k, v in indexes.get(key, {}).get(code, {}).items() if v is not None})
        quote = quotes.get(code) or {}
        row.update(close=quote.get("close"), price_date=quote.get("date"),
                   industry=row.get("industry") or quote.get("industry") or "",
                   turnover=number(meta.get("turnover_rate")), volume_ratio=number(meta.get("vol_ratio")),
                   streak=number(meta.get("连续天数")), first_seen=norm_date(meta.get("初次入选")),
                   rps_delta=number(meta.get("rps_50_chg")))
        # 999 means a newly available RPS reading in the source, not a 999-point move.
        if row["rps_delta"] == 999:
            row["rps_delta"] = None
        for count in (1, 5, 20):
            start = days[-count-1] if len(days) > count else ""
            a, b = lookup.get((start, code)), lookup.get((asof, code))
            row[f"return{count}"] = (b/a-1)*100 if a and b else None
        result.append(row)
    return result, quotes, previous


def select(rows, *, query="", industries=(), membership_filter="全部", only_clean=False,
           intersection=False, max_return5=None, sort="代码", ascending=False):
    fields = {"代码": "symbol", "RPS50": "rps", "5日涨跌幅": "return5", "20日涨跌幅": "return20",
              "换手率": "turnover", "连续在强势榜天数": "streak"}
    from stock_search import matches_stock
    chosen = [r for r in rows
              if matches_stock(query, r["name"], r["symbol"], r.get("industry", ""))
              and (not industries or r.get("industry") in industries)
              and (membership_filter == "全部" or r.get("membership") == membership_filter)
              and (not only_clean or not r["risks"])
              and (not intersection or len(r["sources"]) >= 2)
              and (max_return5 is None or (r.get("return5") is not None and r["return5"] <= max_return5))]
    field = fields.get(sort, "symbol")
    if field == "symbol":
        return sorted(chosen, key=lambda r: r["symbol"])
    present = [r for r in chosen if r.get(field) is not None]
    absent = [r for r in chosen if r.get(field) is None]
    return sorted(present, key=lambda r: r[field], reverse=not ascending) + sorted(absent, key=lambda r: r["symbol"])

"""Dated observations for a retail investor: facts, interpretation, next check.

Pure calculations. Cutoffs and cohorts are selected before observing outcomes;
missing sessions or prices never become zeros or fabricated continuity.
"""
from __future__ import annotations

import re

import pandas as pd

from data_freshness import norm_date
from decision_workbench import number, symbol_id
from ladder_themes import attribution_for
from narrative import _theme
from strategy_playbooks import PLAYBOOKS

RULE_VERSION = "2026-11-20.3"

NON_INDUSTRIAL = {"AB股", "ST股", "创业成份", "低价股", "百元股", "科创板做市股", "微盘股",
                  "红利破净股", "周期股", "微利股", "小盘股", "微盘精选", "权重股", "大盘股", "中盘股",
                  "超跌股", "破增发价股", "基金减仓", "基金增仓", "股权集中", "股权分散", "趋势股",
                  "反转股", "题材股", "高成长股", "券商金股", "近期新高", "历史新高", "百日新高",
                  "转债标的", "行业龙头", "HS300_", "深成500", "上证50_", "上证180_", "中证500",
                  "上证380", "创业板综", "深证100R", "标准普尔", "宁组合", "密集调研", "外资背景",
                  "连续亏损", "高市净率", "高市盈率", "增持计划", "近期摘帽"}


def sessions(calendar, asof, count):
    asof = norm_date(asof)
    if not asof or not isinstance(calendar, dict):
        return []
    start, end = norm_date(calendar.get("start_date")), norm_date(calendar.get("end_date"))
    raw = calendar.get("open_dates")
    if not start or not end or not start <= asof <= end or not isinstance(raw, list):
        return []
    dates = [norm_date(d) for d in raw]
    if any(not d or not start <= d <= end for d in dates) or asof not in dates:
        return []
    return sorted({d for d in dates if d <= asof})[-count:]


def dated(frame, asof):
    if frame is None or frame.empty or "date" not in frame:
        return pd.DataFrame()
    result = frame.copy()
    result["date"] = result["date"].map(norm_date)
    return result[(result["date"] != "") & (result["date"] <= asof)]


def price_lookup(prices, asof):
    p = dated(prices, asof)
    if p.empty or not {"ts_code", "adj_close"} <= set(p):
        return {}
    p["symbol"] = p["ts_code"].map(symbol_id)
    p["adj_close"] = p["adj_close"].map(number)
    p = p.drop_duplicates(["date", "symbol", "adj_close"])
    p = p[~p.duplicated(["date", "symbol"], keep=False)]
    return {(r.date, r.symbol): r.adj_close for r in p.itertuples()
            if r.symbol and number(r.adj_close) is not None and r.adj_close > 0}


def cohort_result(codes, lookup, start, end):
    codes = sorted(set(codes) - {""})
    values = []
    for code in codes:
        a, b = lookup.get((start, code)), lookup.get((end, code))
        if a is not None and b is not None:
            values.append((code, (b / a - 1) * 100))
    pcts = [pct for _, pct in values]
    a, b = lookup.get((start, "000300.SH")), lookup.get((end, "000300.SH"))
    benchmark = (b / a - 1) * 100 if a is not None and b is not None else None
    med = float(pd.Series(pcts).median()) if pcts else None
    return dict(base=len(codes), n=len(pcts), missing=len(codes) - len(pcts),
                median=med, up_ratio=sum(v > 0 for v in pcts) / len(pcts) if pcts else None,
                big_loss=sum(v <= -5 for v in pcts) if pcts else None,
                benchmark=benchmark, excess=med - benchmark if med is not None and benchmark is not None else None,
                returns=dict(values))


def style_outcomes(picks, plans, prices, calendar, asof):
    """Each day checks only the prior session's archived membership."""
    days = sessions(calendar, asof, 6)
    lookup = price_lookup(prices, asof)
    picks = dated(picks, asof)
    result = {}
    for style, book in PLAYBOOKS.items():
        if style == "fundamental":
            continue
        series = []
        for start, end in zip(days, days[1:]):
            codes, labels, missing_sources = set(), [], []
            strategies = ("rps", "breakout") if style == "trend" else ("etf",) if style == "etf" else ()
            if not picks.empty and {"strategy", "ts_code"} <= set(picks):
                for strategy in strategies:
                    rows = picks[(picks["date"] == start) & (picks["strategy"] == strategy)]
                    if not rows.empty:
                        labels.append({"rps": "RPS归档前60", "breakout": "新高归档前60", "etf": "ETF归档前60"}[strategy])
                        codes.update(rows["ts_code"].map(symbol_id))
                    else:
                        missing_sources.append(strategy)
            elif strategies:
                missing_sources.extend(strategies)
            plan = plans.get(start) or {}
            if norm_date(plan.get("date")) == start and plan.get("status") in ("complete", "partial", "ok"):
                for key in book["pools"]:
                    pool = (plan.get("pools") or {}).get(key)
                    if isinstance(pool, dict):
                        labels.append(str(pool.get("title") or key))
                        codes.update(symbol_id(row.get("code"), equity=True) for row in pool.get("items", []))
                    else:
                        missing_sources.append(key)
            elif book["pools"]:
                missing_sources.extend(book["pools"])
            stats = cohort_result(codes, lookup, start, end)
            stats.update(date=end, selected_on=start, sources=labels, missing_sources=missing_sources,
                         coverage=stats["n"] / stats["base"] if stats["base"] else None)
            stats["usable"] = stats["n"] > 0 and stats["missing"] == 0 and not missing_sources
            series.append(stats)
        result[style] = series
    return result


def theme_continuity(history, calendar, asof, top_n=10):
    history = dated(history, asof)
    empty = dict(date=asof, rows=[], checked=0, expected=5, missing=[], followthrough=None)
    if history.empty or not {"code", "name", "pct_chg", "pct_10d"} <= set(history):
        return empty
    history = history[history.apply(lambda r: _theme(r.to_dict()) and "持股" not in str(r["name"])
                                   and not any(word in str(r["name"]) for word in ("新进", "季报", "中报", "年报"))
                                   and r["name"] not in NON_INDUSTRIAL, axis=1)].copy()
    history = history.drop_duplicates()
    history = history[~history.duplicated(["date", "code"], keep=False)]
    for col in ("pct_chg", "pct_5d", "pct_10d", "pct_20d", "up_count", "down_count"):
        history[col] = pd.to_numeric(history[col], errors="coerce") if col in history else float("nan")
    days = sessions(calendar, asof, 5)
    if not days:
        return dict(empty, missing=["交易日历未覆盖，不能核对持续性。"])
    daily, leaders = {}, {}
    # Snapshot breadth must be comparable with recent snapshots; partial days
    # cannot promote survivors into the top group. Explicit 90% coverage floor.
    sizes = history.groupby("date")["code"].nunique()
    reference = int(sizes.tail(20).max()) if len(sizes) else 0
    missing = []
    for day in days:
        group = history[history["date"] == day].dropna(subset=["pct_chg", "pct_10d"])
        if len(group) < max(top_n, reference * .9):
            missing.append(day)
            continue
        daily[day] = group.set_index("code").to_dict("index")
        leaders[day] = set(group.sort_values(["pct_10d", "code"], ascending=[False, True]).head(top_n)["code"])
    if asof not in daily:
        return dict(empty, missing=missing, checked=len(daily))
    today = daily[asof]
    median = float(pd.Series([r["pct_chg"] for r in today.values()]).median())
    previous = days[-2] if len(days) >= 2 else None
    universe = set(leaders[asof]) | (leaders.get(previous, set()))
    universe |= set(sorted(today, key=lambda c: (-today[c]["pct_chg"], c))[:5])
    rows = []
    for code in universe:
        if code not in today:
            continue
        r = today[code]
        available = [d for d in days if d in daily and code in daily[d]]
        appearances = sum(code in leaders[d] for d in available)
        streak = 0
        for d in reversed(days):
            if d not in leaders or code not in leaders[d]:
                break
            streak += 1
        up, down = number(r["up_count"]), number(r["down_count"])
        breadth = up / (up + down) if up is not None and down is not None and up >= 0 and down >= 0 and up + down else None
        old = daily.get(previous, {}).get(code, {})
        pu, pdn = number(old.get("up_count")), number(old.get("down_count"))
        old_ratio = pu / (pu + pdn) if pu is not None and pdn is not None and pu >= 0 and pdn >= 0 and pu + pdn else None
        delta = (breadth - old_ratio) * 100 if breadth is not None and old_ratio is not None else None
        pct5, pct10, today_pct = number(r["pct_5d"]), number(r["pct_10d"]), number(r["pct_chg"])
        state = f"近{len(available)}次快照入榜{appearances}次"
        priority = 0
        check = "对照涨幅、内部上涨占比与入榜记录。"
        rows.append(dict(code=code, name=r["name"], pct=today_pct, pct5=pct5, pct10=pct10,
                         pct20=number(r["pct_20d"]), appearances=appearances, observed=len(available),
                         streak=streak, breadth=breadth, breadth_delta=delta, excess=today_pct-median,
                         state=state, next_check=check, priority=priority))
    follow = None
    if previous in leaders:
        codes = leaders[previous]
        values = [today[c]["pct_chg"] for c in codes if c in today]
        follow = dict(selected_on=previous, base=len(codes), n=len(values),
                      median=float(pd.Series(values).median()) if values else None,
                      market_median=median, retained=len(codes & leaders[asof]))
    return dict(date=asof, rows=sorted(rows, key=lambda r: (r["priority"], -r["appearances"], -r["pct10"], r["code"])),
                checked=len(daily), expected=5, missing=missing, followthrough=follow,
                market_median=median, top_n=top_n)


def theme_tokens(text):
    # Exact labels only. Never infer a theme from an industry or partial word.
    return {t.strip() for t in re.split(r"[+＋、;；,，|]", str(text or "")) if t.strip()}


def attributed_members(theme, ladder, attribution, date):
    if norm_date(ladder.get("date")) != date:
        return []
    result = []
    for stock in ladder.get("stocks") or []:
        match = attribution_for(stock.get("code"), date, attribution)
        if match and theme in theme_tokens(match["theme"]):
            result.append(dict(symbol=symbol_id(stock.get("code"), equity=True),
                               name=stock.get("name"), boards=number(stock.get("limit_times")),
                               theme=match["theme"], source=match["source"]))
    return result


def theme_leader_followers(theme, archives, current_ladder, current_attribution, prices, calendar, asof):
    days = sessions(calendar, asof, 2)
    current = attributed_members(theme, current_ladder, current_attribution, asof)
    empty = dict(current=current, previous="", leaders=None, followers=None,
                 note="尚缺上一交易日的题材归因归档，不能回填猜测龙头与后排。")
    if len(days) != 2:
        return dict(empty, note="交易日历未覆盖，不能确定比较日期。")
    previous = days[0]
    archive = archives.get(previous) or {}
    members = attributed_members(theme, archive.get("ladder") or {}, archive.get("attribution") or {}, previous)
    if not members or any(r["boards"] is None for r in members):
        return empty
    height = max(r["boards"] for r in members)
    if height < 2:
        return dict(empty, previous=previous, note="昨日已归因样本均为首板，没有连板身位差，不能划分高标与后排。")
    lookup = price_lookup(prices, asof)
    leaders = cohort_result([r["symbol"] for r in members if r["boards"] == height], lookup, previous, asof)
    followers = cohort_result([r["symbol"] for r in members if r["boards"] < height], lookup, previous, asof)
    return dict(current=current, previous=previous, leaders=leaders, followers=followers,
                note="按昨日已归因涨停样本的最高连板数区分高标与其余股票；不代表完整题材成分或已认证龙头。")


def funding_readout(market, margin, calendar, asof):
    hist = dated(market, asof)
    days = sessions(calendar, asof, 26)
    result = dict(date=asof, amount=None, ratio=None, center_change=None, margin=None,
                  facts=[], implications=[], missing=[])
    amounts = {}
    if not hist.empty and "total_amount_yi" in hist:
        hist = hist.drop_duplicates()
        hist = hist[~hist.duplicated("date", keep=False)]
        amounts = {r.date: number(r.total_amount_yi) for r in hist.itertuples()}
    if number(amounts.get(asof)) is not None and amounts[asof] > 0:
        result["amount"] = amounts[asof]
        result["facts"].append(f"市场成交额 {amounts[asof]:,.0f} 亿元。")
    if len(days) >= 21 and all(amounts.get(d) is not None and amounts[d] > 0 for d in days[-21:]):
        previous_mean = sum(amounts[d] for d in days[-21:-1]) / 20
        result["ratio"] = amounts[asof] / previous_mean
        result["facts"].append(f"成交额为前20个交易日均额的 {result['ratio']:.2f} 倍。")
    else:
        result["missing"].append("成交额缺少连续21个交易日，暂不比较量能中枢。")
    if len(days) == 26 and all(amounts.get(d) is not None and amounts[d] > 0 for d in days):
        current_center = sum(amounts[d] for d in days[-20:]) / 20
        old_center = sum(amounts[d] for d in days[-25:-5]) / 20
        result["center_change"] = (current_center / old_center - 1) * 100
        result["facts"].append(f"20日成交均额较5个交易日前变化 {result['center_change']:+.2f}%。")
    m = dated(margin, asof)
    if not m.empty and {"balance_yi", "source"} <= set(m):
        m = m[m["source"] == "tushare.margin:SSE+SZSE"].drop_duplicates()
        m = m[~m.duplicated("date", keep=False)].sort_values("date")
        m["balance_yi"] = m["balance_yi"].map(number)
        m = m[m["balance_yi"].notna() & (m["balance_yi"] > 0)]
        if not m.empty:
            latest = m.iloc[-1]
            lag_dates = sessions(calendar, asof, 2)
            usable = latest["date"] in lag_dates
            margin_days = sessions(calendar, latest["date"], 6)
            lookup = dict(zip(m["date"], m["balance_yi"]))
            change = (lookup[margin_days[-1]] / lookup[margin_days[0]] - 1) * 100 if len(margin_days) == 6 and all(d in lookup for d in margin_days) else None
            result["margin"] = dict(date=latest["date"], balance=float(latest["balance_yi"]), change5=change, usable=usable)
            if not usable:
                result["missing"].append(f"融资余额停在 {latest['date']}，超过允许的一个交易日披露滞后，不用于当前判断。")
    if result["margin"] is None:
        result["missing"].append("融资余额尚无可核验数据，不能判断杠杆资金是否收缩。")
    if result["ratio"] is not None:
        if result["ratio"] < 1:
            result["implications"].append("本日成交额低于前20个交易日均额。")
        else:
            result["implications"].append("本日成交额高于或等于前20个交易日均额。")
    mr = result["margin"]
    if mr and mr["usable"] and mr["change5"] is not None:
        result["implications"].append("融资余额近5个交易日" + ("下降，杠杆资金余额在收缩；高波动方向需再核对承接。" if mr["change5"] < 0 else "增加；需结合成交与价格，不能单凭余额增加认定行情走强。"))
    return result


def style_assessment(style, sentiment, breadth_history, outcomes, funding, calendar, asof):
    """Describe observed membership and coverage, without issuing trading permission."""
    sample = next((r for r in outcomes.get(style, []) if r["date"] == asof), None)
    facts, missing = [], []
    if sample:
        facts.append(f"昨日归档 {sample.get('base', 0)} 只，已核对价格 {sample.get('n', 0)} 只，"
                     f"缺失 {sample.get('missing', 0)} 只")
        if sample.get("median") is not None:
            win_rate = sample.get("up_ratio")
            suffix = f"，上涨占比 {win_rate:.1%}" if win_rate is not None else ""
            facts.append(f"中位涨跌幅 {sample['median']:+.2f}%{suffix}")
        if sample.get("missing_sources"):
            missing.append("部分候选来源未归档，统计仅覆盖已归档名单。")
    valid = norm_date(sentiment.get("date")) == asof
    breadth = (sentiment.get("breadth") or {}) if valid else {}
    up = number(breadth.get("up_ratio"))
    if up is not None:
        facts.append(f"全市场上涨占比 {up:.1%}")
    label = "已归档名单的后续表现" if sample and sample.get("n") else "暂无可核对价格"
    return dict(label=label, tone="info", facts=facts, missing=missing,
                conclusion="；".join(facts), next_check="可展开逐股涨跌幅，或查看所选股票的技术图。")


def focus_changes(record, current, prices, asof):
    p = record["plan"]
    before = p.get("candidate_evidence") or {}
    base_date = p["asof"]
    start = {str(v) for v in before.get("sources", [])}
    end = {str(v) for v in (current or {}).get("sources", [])}
    risks_before = set(before.get("risks") or [])
    risks_after = set((current or {}).get("risks") or [])
    lookup = price_lookup(prices, asof)
    a, b = lookup.get((base_date, p["symbol"])), lookup.get((asof, p["symbol"]))
    return dict(baseline=base_date, date=asof, new_sources=sorted(end-start), lost_sources=sorted(start-end),
                new_risks=sorted(risks_after-risks_before),
                rps_before=number(before.get("rps")), rps_now=number((current or {}).get("rps")),
                return_pct=(b/a-1)*100 if a is not None and b is not None and asof > base_date else None,
                original_reasons=before.get("reasons") or [p.get("thesis", "")])

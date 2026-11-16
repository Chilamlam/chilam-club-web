# -*- coding: utf-8 -*-
"""收盘复盘：只写可核对的变化，统计标签和模型阈值不进入正文。

文案约束见 REVIEW_CONTENT.md。历史比较必须匹配交易日历中的前一交易日；
板块相对强弱只与同日、同源的题材板块比较，不拿个股中位数当指数。
"""
from __future__ import annotations

import json
import math
import os
import re

import pandas as pd

from data_freshness import norm_date
from sector_rotation import is_style_board

DERIVED_PATH = os.path.join("data", "sentiment", "derived.json")
SECTOR_PATH = os.path.join("data", "sector_rotation", "analysis.json")
LADDER_PATH = os.path.join("data", "limit_ladder.json")
BREADTH_HISTORY = os.path.join("data", "sentiment", "breadth_history.csv")
SECTOR_HISTORY = os.path.join("data", "sector_rotation", "history.csv")
CALENDAR_PATH = os.path.join("data", "trading_calendar.json")

# 排名/风格集合不是产业题材，不能充当“今天炒什么”的答案。
NON_THEME_NAMES = {"东方财富热股", "最近多板", "价值股", "红利股", "高股息精选",
                   "茅指数", "央视50_", "低市盈率", "低市净率", "超级品牌"}


def _load(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _load_history(path: str):
    try:
        return pd.read_csv(path, dtype={"date": str, "code": str})
    except (OSError, ValueError, pd.errors.ParserError):
        return None


def _date_display(value: str) -> str:
    date = norm_date(value)
    return f"{date[:4]}-{date[4:6]}-{date[6:]}" if date else "日期未确认"


def _number(value) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def _count(value) -> int | None:
    n = _number(value)
    return int(n) if n is not None and n >= 0 and n.is_integer() else None


def _pct(value, digits: int = 2) -> str:
    return f"{float(value):+.{digits}f}%"


def _day(history, date: str) -> list[dict]:
    if history is None or history.empty or "date" not in history or not date:
        return []
    return history[history["date"].map(norm_date) == date].to_dict("records")


def _previous_date(calendar, date: str) -> str | None:
    if not isinstance(calendar, dict):
        return None
    start, end = norm_date(calendar.get("start_date")), norm_date(calendar.get("end_date"))
    raw = calendar.get("open_dates")
    if not start or not end or not start <= date <= end or not isinstance(raw, list):
        return None
    days = [norm_date(d) for d in raw]
    if not days or not all(d and start <= d <= end for d in days) or date not in days:
        return None
    return max((d for d in days if d < date), default=None)


def _theme(row: dict) -> bool:
    name = row.get("name")
    return (isinstance(name, str) and bool(name) and not is_style_board(name)
            and name not in NON_THEME_NAMES and "风格" not in name
            and not re.fullmatch(r"[大中小]盘(价值|成长|平衡)", name))


def _relay(derived: dict) -> str:
    """小样本接力统计独立展示，不推断资金意愿或可实现收益。"""
    parts = []
    r = ((derived.get("promotion") or {}).get("rates") or {}).get("1进2") or {}
    base, promoted = _count(r.get("base")), _count(r.get("promoted"))
    if base and promoted is not None and promoted <= base:
        parts.append(f"昨日首板股有 **{promoted}/{base}** 今天继续涨停（{promoted / base:.1%}）。")
    p = derived.get("premium") or {}
    n, med, share = _count(p.get("n")), _number(p.get("median_pct")), _number(p.get("win_rate"))
    if p.get("status") in ("ok", "insufficient") and n and med is not None:
        text = f"昨日连板股中，取得行情的 {n} 只今天中位涨跌幅 **{_pct(med)}**"
        if share is not None and 0 <= share <= 1:
            text += f"，上涨占比 {share:.0%}"
        missing = _count(p.get("missing"))
        if missing:
            text += f"（另有 {missing} 只行情缺失）"
        parts.append(text + "。")
    gap = derived.get("ladder_gap") or {}
    height, total = _count(gap.get("max_height")), _count(gap.get("total"))
    if gap.get("status") == "ok" and height and total is not None:
        gaps = gap.get("gaps") or []
        parts.append(f"今日涨停 {total} 家，最高 {height} 板；"
                     + (f"中间缺少 {gaps} 板。" if gaps else "中间高度无空档。"))
    return "\n\n".join(parts)


def build_narrative(derived: dict | None, sectors: dict | None,
                    ladder: dict | None, *, breadth_history=None,
                    sector_history=None, calendar=None) -> dict:
    """正文只保留市场变化、当日领涨和近期强势方向；接力数据另给明细。"""
    empty = {"status": "unavailable", "date": "", "markdown": "", "relay_markdown": "", "missing": []}
    if not isinstance(derived, dict) or derived.get("status") not in ("ok", "complete", "incomplete"):
        return dict(empty, missing=["情绪数据未生成"])
    date = norm_date(derived.get("date"))
    if not date:
        return dict(empty, missing=["情绪数据缺少有效统计日"])
    previous = _previous_date(calendar, date)
    missing, paragraphs = [], []
    if not previous:
        missing.append("交易日历未覆盖，省略跨日比较")

    breadth = derived.get("breadth") or {}
    up, down, median = _count(breadth.get("up")), _count(breadth.get("down")), _number(breadth.get("median_pct"))
    if breadth.get("status") == "ok" and up is not None and down is not None and median is not None:
        mood = "下跌家数多于上涨" if down > up else "上涨家数多于下跌" if up > down else "涨跌家数相同"
        text = f"**市场整体：{mood}。** 上涨 {up} 家、下跌 {down} 家，个股涨跌幅中位数 **{_pct(median)}**。"
        old = _day(breadth_history, previous)
        if len(old) == 1 and _count(old[0].get("up")) is not None and _number(old[0].get("median_pct")) is not None:
            du, dm = up - _count(old[0]["up"]), median - _number(old[0]["median_pct"])
            change = f"上涨家数{'增加' if du > 0 else '减少'} {abs(du)} 家" if du else "上涨家数不变"
            change += (f"，中位涨跌幅{'提高' if dm > 0 else '降低'} {abs(dm):.2f} 个百分点" if abs(dm) >= .005 else "，中位涨跌幅持平")
            text += f" 较上一交易日（{_date_display(previous)}），{change}。"
        elif previous:
            missing.append(f"{_date_display(previous)} 市场数据缺失，省略跨日比较")
        paragraphs.append(text)
    else:
        missing.append("市场涨跌家数或中位涨跌幅缺失")

    if not sectors or sectors.get("status") != "ok" or norm_date(sectors.get("date")) != date:
        sectors = None
        missing.append("板块数据缺失、状态异常或与情绪统计日不同")
    top = [r for r in (sectors or {}).get("top", []) if isinstance(r, dict) and _theme(r)
           and _number(r.get("pct_chg")) is not None and _number(r.get("pct_10d")) is not None]
    if (sectors or {}).get("window_col") != "pct_10d":
        top = []
    today = {str(r["code"]): r for r in _day(sector_history, date)
             if r.get("code") and _theme(r) and _number(r.get("pct_chg")) is not None} if sectors else {}
    old_sectors = {str(r["code"]): r for r in _day(sector_history, previous) if r.get("code")}
    # 历史快照不足分析产物所覆盖的数量时，不把一个残缺子集叫作全榜。
    expected = _count((sectors or {}).get("universe_count"))
    if not expected or len(today) < expected * .9:
        today = {}
    benchmark = float(pd.Series([_number(r["pct_chg"]) for r in today.values()]).median()) if today else None
    if today:
        leaders = sorted(today.values(), key=lambda r: (-float(r["pct_chg"]), str(r["code"])))[:3]
        title = "当日领涨题材" if float(leaders[0]["pct_chg"]) > 0 else "当日跌幅较小的题材" if float(leaders[0]["pct_chg"]) < 0 else "当日表现靠前的题材"
        paragraphs.append(f"**{title}**（本站统计 {len(today)} 个题材板块）："
                          + "、".join(f"{r['name']} {_pct(r['pct_chg'])}" for r in leaders) + "。")
    elif sectors:
        missing.append("当日板块全量快照不足，省略当日领涨榜和相对强弱比较")

    if top:
        lines = []
        for r in top[:2]:
            pct = float(r["pct_chg"])
            line = f"- **{r['name']}**：近10日 {_pct(r['pct_10d'])}，今日 {_pct(pct)}"
            # 只使用同一代码且当天涨幅一致的快照，防止两个批次拼出一条结论。
            row = today.get(str(r.get("code")), {})
            if row and abs(float(row["pct_chg"]) - pct) <= .011:
                diff = pct - benchmark
                if abs(diff) >= .005:
                    line += f"，{'高于' if diff > 0 else '低于'}题材中位数 {abs(diff):.2f} 个百分点"
                else:
                    line += "，与题材中位数持平"
                u, d = _count(row.get("up_count")), _count(row.get("down_count"))
                if u is not None and d is not None:
                    line += f"；内部 {u} 涨 / {d} 跌"
                prev = old_sectors.get(str(r.get("code")), {})
                pu, pdn = _count(prev.get("up_count")), _count(prev.get("down_count"))
                if u is not None and d is not None and u + d and pu is not None and pdn is not None and pu + pdn:
                    delta = (u / (u + d) - pu / (pu + pdn)) * 100
                    if abs(delta) >= .05:
                        line += f"，上涨占比较上一交易日{'增加' if delta > 0 else '减少'} {abs(delta):.1f} 个百分点（剔除平盘）"
            lines.append(line + "。")
        baseline = f"同日题材涨跌幅中位数 {_pct(benchmark)}。" if benchmark is not None else ""
        paragraphs.append("**近10日表现靠前的题材**：" + baseline + "\n" + "\n".join(lines))
    elif sectors:
        missing.append("近10日题材强度数据缺失")

    relay = _relay(derived)
    return {"status": "ok" if paragraphs or relay else "unavailable", "date": date,
            "markdown": "\n\n".join(paragraphs), "relay_markdown": relay, "missing": missing}


def load_narrative() -> dict:
    return build_narrative(_load(DERIVED_PATH), _load(SECTOR_PATH), _load(LADDER_PATH),
                           breadth_history=_load_history(BREADTH_HISTORY),
                           sector_history=_load_history(SECTOR_HISTORY), calendar=_load(CALENDAR_PATH))

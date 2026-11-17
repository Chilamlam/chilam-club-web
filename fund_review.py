"""Dated fund-price / published NAV comparison. No intraday IOPV assumptions."""
from __future__ import annotations

import math

from data_freshness import norm_date


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if not isinstance(value, bool) and math.isfinite(result) else None


def nav_comparison(price, quote_date, rows, *, code: str) -> dict:
    date = norm_date(quote_date)
    value = number(price)
    base = dict(quote_date=date, nav_date="", nav=None, premium_rate=None,
                reference_gap=None, nav_status="净值缺失")
    if not date or value is None or value <= 0:
        return {**base, "nav_status": "价格或日期无效"}
    valid = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("ts_code")) != code:
            continue
        day = norm_date(row.get("nav_date"))
        nav = number(row.get("unit_nav"))
        if day and day <= date and nav is not None and nav > 0:
            valid.append((day, nav))
    if not valid:
        return base
    latest = max(day for day, _ in valid)
    values = {nav for day, nav in valid if day == latest}
    if len(values) != 1:
        return {**base, "nav_date": latest, "nav_status": "同日净值冲突"}
    nav = values.pop()
    gap = (value / nav - 1) * 100
    if latest == date:
        return {**base, "nav_date": latest, "nav": nav, "premium_rate": round(gap, 3),
                "nav_status": "同日净值"}
    return {**base, "nav_date": latest, "nav": nav, "reference_gap": round(gap, 3),
            "nav_status": "净值滞后，仅供对照"}


def display_funds(rows) -> list[dict]:
    """Old snapshots without dated NAV cannot silently display a zero premium."""
    result = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        quote, nav_day = norm_date(row.get("quote_date")), norm_date(row.get("nav_date"))
        checked = nav_comparison(row.get("price"), quote,
                                 [{"ts_code": row.get("code"), "nav_date": nav_day,
                                   "unit_nav": row.get("nav")}], code=row.get("code"))
        # Recompute rather than trusting a stored zero or a stale percentage.
        row.update(checked)
        if not quote or not nav_day:
            status = raw.get("nav_status")
            row["nav_status"] = status if status in ("净值缺失", "净值接口暂不可用", "同日净值冲突") else "旧快照缺少日期，待重新核验"
        elif nav_day > quote:
            row["nav_status"] = "净值日期晚于价格，未作比较"
        row.pop("est_premium", None)
        result.append(row)
    return result

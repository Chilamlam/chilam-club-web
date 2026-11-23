"""Dated, comparable observations for market breadth, futures and ETF turnover."""
from __future__ import annotations

import datetime as dt
import math
import re

import pandas as pd

from data_freshness import norm_date

INDEXES = {
    "IH": {"name": "上证50", "secid": "1.000016"},
    "IF": {"name": "沪深300", "secid": "1.000300"},
    "IC": {"name": "中证500", "secid": "1.000905"},
    "IM": {"name": "中证1000", "secid": "1.000852"},
}
# Explicit observation universe, not an estimate of any institution's holdings.
ETF_POOL = {
    "510050.SH": "上证50", "510100.SH": "上证50", "510300.SH": "沪深300",
    "510310.SH": "沪深300", "510330.SH": "沪深300", "159919.SZ": "沪深300",
    "510500.SH": "中证500", "510510.SH": "中证500", "512500.SH": "中证500",
    "159922.SZ": "中证500", "512100.SH": "中证1000", "159845.SZ": "中证1000",
    "159629.SZ": "中证1000", "159338.SZ": "中证A500", "159339.SZ": "中证A500",
    "563360.SH": "中证A500", "159915.SZ": "创业板指", "159952.SZ": "创业板指",
    "588000.SH": "科创50", "588080.SH": "科创50",
}
ETF_NAMES = {
    "510050.SH":"上证50ETF华夏", "510100.SH":"上证50ETF易方达",
    "510300.SH":"沪深300ETF华泰柏瑞", "510310.SH":"沪深300ETF易方达",
    "510330.SH":"沪深300ETF华夏", "159919.SZ":"沪深300ETF嘉实",
    "510500.SH":"中证500ETF南方", "510510.SH":"中证500ETF广发",
    "512500.SH":"中证500ETF华夏", "159922.SZ":"中证500ETF嘉实",
    "512100.SH":"中证1000ETF南方", "159845.SZ":"中证1000ETF华夏",
    "159629.SZ":"中证1000ETF富国", "159338.SZ":"中证A500ETF国泰",
    "159339.SZ":"A500ETF银华", "563360.SH":"A500ETF华泰柏瑞",
    "159915.SZ":"创业板ETF易方达", "159952.SZ":"创业板ETF广发",
    "588000.SH":"科创50ETF华夏", "588080.SH":"科创50ETF易方达",
}


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def dated(frame, *, keys=("date",)):
    if frame is None or frame.empty or "date" not in frame:
        return pd.DataFrame()
    frame = frame.copy()
    frame["date"] = frame["date"].map(norm_date)
    frame = frame[frame["date"] != ""].drop_duplicates()
    # Conflicting snapshots must not silently choose a winner.
    return frame[~frame.duplicated(list(keys), keep=False)].sort_values(list(keys))


def breadth_history(breadth, market):
    b, m = dated(breadth), dated(market)
    if b.empty:
        return b
    cols = [c for c in ("date", "up", "down", "median_pct", "mean_pct") if c in b]
    result = b[cols].copy()
    if not m.empty and "total_amount_yi" in m:
        result = result.merge(m[["date", "total_amount_yi"]], on="date", how="left", validate="one_to_one")
    for col in result.columns.difference(["date"]):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result["up_ratio"] = result["up"] / (result["up"] + result["down"]).replace(0, float("nan")) * 100
    return result


def financing_history(frame, calendar):
    frame = dated(frame)
    if frame.empty:
        return frame
    frame["balance_yi"] = pd.to_numeric(frame["balance_yi"], errors="coerce")
    frame = frame[frame["balance_yi"].gt(0)].copy()
    days = sorted(set(calendar.get("open_dates") or []))
    previous = dict(zip(days[1:], days[:-1]))
    lookup = frame.set_index("date")["balance_yi"].to_dict()
    frame["change_yi"] = [row.balance_yi - lookup[previous[row.date]]
        if previous.get(row.date) in lookup else None for row in frame.itertuples()]
    return frame


def expiry(contract, calendar):
    match = re.fullmatch(r"(?:IH|IF|IC|IM)(\d{2})(\d{2})", contract)
    if not match:
        raise ValueError("不是股指期货月份合约")
    first = dt.date(2000+int(match[1]), int(match[2]), 1)
    day = first + dt.timedelta(days=(4-first.weekday()) % 7 + 14)
    end, start = norm_date(calendar.get("end_date")), norm_date(calendar.get("start_date"))
    key = day.strftime("%Y%m%d")
    if start <= key <= end:
        available = sorted(d for d in calendar.get("open_dates", []) if d >= key)
        if available:
            return dt.datetime.strptime(available[0], "%Y%m%d").date(), "第三个周五，按交易日历顺延"
    return day, "第三个周五估算，节假日待核对"


def basis_rows(futures, indexes, calendar):
    indexes = dated(pd.DataFrame(indexes), keys=("prefix", "date"))
    futures = dated(pd.DataFrame(futures), keys=("contract", "date"))
    lookup = {(r["date"], r["prefix"]): numeric(r.get("close")) for r in indexes.to_dict("records")}
    result = []
    for r in futures.to_dict("records"):
        day = norm_date(r.get("date"))
        contract = str(r.get("contract") or "")
        prefix = contract[:2]
        future, spot = numeric(r.get("close")), lookup.get((day, prefix))
        if not day or prefix not in INDEXES or not future or future <= 0 or not spot or spot <= 0:
            continue
        try:
            end, note = expiry(contract, calendar)
        except ValueError:
            continue
        remaining = (end - dt.datetime.strptime(day, "%Y%m%d").date()).days
        if remaining < 0:
            continue
        discount = spot-future
        pct = discount/spot*100
        result.append(dict(date=day, prefix=prefix, index=INDEXES[prefix]["name"], contract=contract,
            spot=spot, future=future, discount=discount, discount_pct=pct,
            annual_pct=pct*365/remaining if remaining else None, days=remaining,
            expiry=end.isoformat(), expiry_note=note, open_interest=numeric(r.get("open_interest"))))
    return result


def net_basis(row, dividend_points):
    dividend = numeric(dividend_points)
    if dividend is None or dividend < 0:
        return None, None
    discount = row["discount"] - dividend
    return discount, discount/row["spot"]*100*365/row["days"] if row["days"] > 0 else None


def etf_activity(frame, calendar):
    frame = dated(frame, keys=("symbol", "date"))
    if frame.empty:
        return frame
    frame["amount_yi"] = pd.to_numeric(frame["amount_yi"], errors="coerce")
    frame.loc[~frame["amount_yi"].map(lambda v: numeric(v) is not None and v >= 0), "amount_yi"] = float("nan")
    days = sorted(set(calendar.get("open_dates") or []))
    positions = {day: i for i, day in enumerate(days)}
    result = []
    for code, group in frame.groupby("symbol"):
        lookup = {r["date"]: numeric(r.get("amount_yi")) for r in group.to_dict("records")}
        for row in group.to_dict("records"):
            i = positions.get(row["date"], -1)
            prior = days[i-20:i] if i >= 20 else []
            values = [lookup.get(d) for d in prior]
            valid = len(values) == 20 and all(v is not None and v >= 0 for v in values)
            mean = sum(values)/20 if valid else None
            amount = numeric(row.get("amount_yi"))
            row.update(mean20_yi=mean, amount_ratio=amount/mean if amount is not None and mean else None,
                       baseline_days=sum(v is not None and v >= 0 for v in values))
            result.append(row)
    return pd.DataFrame(result).sort_values(["date", "symbol"])


def etf_pool_history(frame, symbols):
    result = []
    symbols = set(symbols)
    if frame.empty or not symbols:
        return pd.DataFrame()
    for date, day in frame[frame["symbol"].isin(symbols)].groupby("date"):
        coverage = day["symbol"].nunique()
        full = coverage == len(symbols) and day["amount_yi"].notna().all()
        base = full and day["mean20_yi"].notna().all()
        total = float(day["amount_yi"].sum()) if full else None
        mean = float(day["mean20_yi"].sum()) if base else None
        result.append(dict(date=date, amount_yi=total, mean20_yi=mean,
            amount_ratio=total/mean if mean and total is not None else None,
            coverage=coverage, expected=len(symbols)))
    return pd.DataFrame(result, columns=["date", "amount_yi", "mean20_yi", "amount_ratio", "coverage", "expected"]).sort_values("date")

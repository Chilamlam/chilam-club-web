"""ETF creations/redemptions are observations, never investor identification."""
from __future__ import annotations

import pandas as pd

from data_freshness import norm_date
from market_watch import ETF_POOL, ETF_NAMES, numeric

POOL = dict(ETF_POOL, **{
    "588300.SH": "科创创业50", "588400.SH": "科创创业50",
    "159781.SZ": "科创创业50", "159783.SZ": "科创创业50",
})
NAMES = dict(ETF_NAMES, **{
    "588300.SH": "科创创业50ETF招商", "588400.SH": "科创创业ETF嘉实",
    "159781.SZ": "科创创业ETF易方达", "159783.SZ": "科创创业50ETF华夏",
})
DUAL = {"创业板指", "科创50", "科创创业50"}
TECH = ["半导体", "元器件", "软件服务", "通信设备", "IT设备", "互联网"]
DISCLOSURES = [
    dict(date="20250407", institution="中央汇金", title="已再次增持ETF的公告",
         url="https://www.huijin-inv.cn/huijin-inv/SC20252/2025-04/1002841.shtml",
         fact="披露已再次增持ETF，未披露各基金代码、买入日期明细及金额。"),
    dict(date="20250408", institution="中央汇金", title="有关负责人答记者问",
         url="https://www.huijin-inv.cn/huijin-inv/SC20252/2025-04/1002847.shtml",
         fact="说明将增持各类市场风格的ETF、加大力度并均衡结构；不提供逐日成交或持仓明细。"),
]


def unique_rows(rows, keys, values):
    """Do not arbitrarily pick a conflicting observation."""
    frame = pd.DataFrame(rows)
    if frame.empty or not set(keys + values) <= set(frame):
        return pd.DataFrame(columns=keys + values)
    frame = frame[keys + values].copy().drop_duplicates()
    frame["date"] = frame["date"].map(norm_date)
    frame = frame[frame.date.ne("")]
    return frame[~frame.duplicated(keys, keep=False)]


def daily_flows(rows, calendar, cutoff, splits=None):
    """Align exact sessions; NAV must be unadjusted and from the same date."""
    cutoff = norm_date(cutoff)
    days = sorted({norm_date(d) for d in calendar.get("open_dates", []) if norm_date(d) <= cutoff})
    frame = unique_rows(rows, ["symbol", "date"], ["shares", "nav"])
    frame = frame[frame.symbol.isin(POOL) & frame.date.isin(days)]
    for col in ("shares", "nav"):
        frame[col] = pd.to_numeric(frame[col].map(numeric), errors="coerce").where(lambda s: s.gt(0))
    pieces = []
    for code, group in frame.groupby("symbol"):
        h = group.set_index("date").reindex(days).rename_axis("date")
        h["symbol"] = code
        h["index"] = POOL[code]
        h["change"] = h.shares.diff()
        h["split"] = h.index.isin((splits or {}).get(code, {}).get("dates", []))
        # A known split is not a creation. Also exclude anomalous NAV/share jumps
        # until checked; do not silently call a re-denomination a cash flow.
        h["unusual"] = h.shares.pct_change(fill_method=None).abs().gt(.5) | h.nav.pct_change(fill_method=None).abs().gt(.4)
        checked = (splits or {}).get(code, {}).get("status") == "ok"
        valid = h.change.notna() & h.nav.notna() & ~h["split"] & ~h.unusual & checked
        h["flow_yi"] = (h.change * h.nav / 1e8).where(valid)
        h["shares_yi"] = h.shares / 1e8
        h["assets_yi"] = h.shares * h.nav / 1e8
        pieces.append(h.reset_index())
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def summarize(frame, symbols, start, end):
    """start is the baseline close; flows run from the next session through end."""
    result = []
    if frame.empty:
        return result
    for code in symbols:
        h = frame[(frame.symbol == code) & (frame.date <= end)].sort_values("date")
        if h.empty:
            continue
        a, b = h[h.date == start], h[h.date == end]
        window = h[(h.date > start) & (h.date <= end)]
        s0 = numeric(a.iloc[0].shares) if len(a) else None
        s1 = numeric(b.iloc[0].shares) if len(b) else None
        split = bool(window["split"].any())
        complete = len(window) > 0 and window.flow_yi.notna().all()
        change = s1-s0 if s0 and s1 and not split else None
        valid = h[h.shares.notna()]
        peak = numeric(valid.shares.max())
        # Splits before this archive began do not change units within the
        # observed history; only a split inside it invalidates the peak ratio.
        history_split = bool(h.loc[h.date.ge(valid.date.min()), "split"].any()) if len(valid) else False
        rolling = h.flow_yi.rolling(len(window), min_periods=len(window)).sum() if len(window) else pd.Series(dtype=float)
        history = rolling.dropna()
        flow = float(window.flow_yi.sum()) if complete else None
        rank = 100 * float((history <= flow).mean()) if flow is not None and len(history) else None
        result.append(dict(symbol=code, name=NAMES[code], index=POOL[code], shares_yi=s1/1e8 if s1 else None,
            change_yi=change/1e8 if change is not None else None,
            change_pct=change/s0*100 if change is not None else None,
            flow_yi=flow, observed=int(window.flow_yi.notna().sum()), expected=len(window),
            assets_yi=numeric(b.iloc[0].assets_yi) if len(b) else None,
            peak_ratio=s1/peak*100 if s1 and peak and not history_split else None,
            history_start=valid.date.min() if len(valid) else "", history_count=len(valid),
            flow_percentile=rank, comparable_windows=len(history), split=split,
            suspect=int(window.unusual.sum())))
    return result


def group_flows(frame, symbols):
    """Fixed pool; do not aggregate partial coverage or double count funds."""
    symbols = sorted(set(symbols))
    if frame.empty or not symbols:
        return pd.DataFrame()
    subset = frame[frame.symbol.isin(symbols)]
    rows = []
    for day, group in subset.groupby("date"):
        all_members = set(group.symbol) == set(symbols)
        coverage = int(group.flow_yi.notna().sum())
        rows.append(dict(date=day, coverage=coverage, expected=len(symbols),
            flow_yi=float(group.flow_yi.sum()) if all_members and coverage == len(symbols) else None,
            assets_yi=float(group.assets_yi.sum()) if all_members and group.assets_yi.notna().all() else None))
    return pd.DataFrame(rows).sort_values("date")


def margin_comparison(rows, tech, start, end):
    """Disclosed stock balances grouped using one explicitly dated classification."""
    frame = unique_rows(rows, ["date", "industry"], ["balance_yi", "count"])
    if frame.empty:
        return [], pd.DataFrame()
    frame["group"] = frame.industry.map(lambda v: "未分类" if v == "未分类" else "所选科技行业" if v in tech else "其他已分类A股")
    daily = frame.groupby(["date", "group"], as_index=False).agg(balance_yi=("balance_yi", "sum"), count=("count", "sum"))
    results = []
    for name, group in daily[daily.date <= end].groupby("group"):
        a, b = group[group.date == start], group[group.date == end]
        if a.empty or b.empty:
            continue
        first, last = a.iloc[0], b.iloc[0]
        peak = group.loc[group.balance_yi.idxmax()]
        results.append(dict(group=name, start=float(first.balance_yi), end=float(last.balance_yi),
            change=float(last.balance_yi-first.balance_yi), pct=float((last.balance_yi/first.balance_yi-1)*100) if first.balance_yi else None,
            peak=float(peak.balance_yi), peak_date=peak.date, from_peak=float(last.balance_yi-peak.balance_yi),
            start_count=int(first['count']), end_count=int(last['count'])))
    return results, daily

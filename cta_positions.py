"""CFTC positions relevant to CTA research, not estimated CTA portfolio weights."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from market_watch import numeric

START = "2012-11-01"
SCHEDULE_URL = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm"
COT_URL = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"
MM_URL = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/DisaggregatedExplanatoryNotes/index.htm"
TFF_URL = "https://www.cftc.gov/sites/default/files/idc/groups/public/@commitmentsoftraders/documents/file/tfmexplanatorynotes.pdf"
MARKETS = {
    "088691": dict(name="黄金", family="disagg", usd_sign=1),
    "085692": dict(name="铜", family="disagg", usd_sign=1),
    "084691": dict(name="白银", family="disagg", usd_sign=1),
    "098662": dict(name="美元指数", family="tff", usd_sign=1),
    "099741": dict(name="欧元", family="tff", usd_sign=-1),
    "097741": dict(name="日元", family="tff", usd_sign=-1),
    "096742": dict(name="英镑", family="tff", usd_sign=-1),
    "232741": dict(name="澳元", family="tff", usd_sign=-1),
    "090741": dict(name="加元", family="tff", usd_sign=-1),
    "092741": dict(name="瑞郎", family="tff", usd_sign=-1),
}
FAMILIES = {
    "disagg": dict(id="72hh-3qpy", name="管理基金（Managed Money）",
                   fields=("m_money_positions_long_all", "m_money_positions_short_all", "m_money_positions_spread")),
    "tff": dict(id="gpe5-46if", name="杠杆基金（Leveraged Funds）",
                fields=("lev_money_positions_long", "lev_money_positions_short", "lev_money_positions_spread")),
}


def frame(rows, cutoff):
    """Exclude ambiguous keys and invalid observations; never fill a missing week."""
    columns = ["date", "code", "long", "short", "spreading", "oi"]
    data = pd.DataFrame(rows)
    if data.empty or not set(columns) <= set(data):
        return pd.DataFrame(columns=columns + ["net", "net_pct"])
    data = data[columns].drop_duplicates()
    data = data[~data.duplicated(["date", "code"], keep=False)].copy()
    data = data[data.code.isin(MARKETS) & data.date.le(cutoff)]
    for key in ("long", "short", "spreading", "oi"):
        data[key] = pd.to_numeric(data[key].map(numeric), errors="coerce")
    data = data[data.oi.gt(0) & data[["long", "short", "spreading"]].ge(0).all(axis=1)
                & (data.long + data.spreading).le(data.oi) & (data.short + data.spreading).le(data.oi)]
    data["net"] = data.long - data.short
    data["net_pct"] = data.net / data.oi * 100
    return data.sort_values(["date", "code"])


def summaries(data, codes, end, years=3, dollar=False):
    result = []
    date = pd.Timestamp(end)
    start = (date - pd.DateOffset(years=years)).strftime("%Y-%m-%d") if years else START
    for code in codes:
        h = data[(data.code == code) & data.date.le(end)].copy()
        latest = h[h.date == end]
        meta = MARKETS[code]
        row = dict(code=code, name=meta["name"], date=end, available=not latest.empty,
                   latest=h.date.max() if len(h) else None)
        if latest.empty:
            result.append(row)
            continue
        now = latest.iloc[0]
        sign = meta["usd_sign"] if dollar else 1
        def past(days):
            previous = h[h.date == (date - pd.Timedelta(days=days)).strftime("%Y-%m-%d")]
            return previous.iloc[0] if len(previous) else None
        week, month = past(7), past(28)
        history = h[h.date.ge(start)]
        values = history.net_pct * sign
        value = float(now.net_pct) * sign
        percentile = (float((values < value).sum()) + .5 * float((values == value).sum())) / len(values) * 100 if len(values) > 1 else None
        row.update(net=int(now.net) * sign, net_pct=value, long=int(now.long), short=int(now.short),
                   spreading=int(now.spreading), oi=int(now.oi),
                   week_net=(int(now.net - week.net) * sign) if week is not None else None,
                   week_pp=float(now.net_pct - week.net_pct) * sign if week is not None else None,
                   month_pp=float(now.net_pct - month.net_pct) * sign if month is not None else None,
                   long_change=int(now.long - week.long) if week is not None else None,
                   short_change=int(now.short - week.short) if week is not None else None,
                   percentile=percentile, samples=len(history), history_start=history.date.min())
        result.append(row)
    return result


def chart_points(data, code, start, end, dollar=False):
    h = data[(data.code == code) & data.date.between(start, end)]
    points = []
    sign = MARKETS[code]["usd_sign"] if dollar else 1
    for row in h.itertuples():
        if points and (pd.Timestamp(row.date) - pd.Timestamp(points[-1]["date"])).days > 7:
            points.append(dict(date=(pd.Timestamp(points[-1]["date"]) + pd.Timedelta(days=7)).strftime("%Y-%m-%d"), value=None))
        points.append(dict(date=row.date, value=float(row.net_pct) * sign))
    return points


def release_state(schedule, now):
    """Official scheduled releases, including holiday delays; never invent actual release times."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    eastern = ZoneInfo("America/New_York")
    year = now.astimezone(eastern).year
    if schedule.get("year") != year:
        return dict(expected=None, next=None)
    events = []
    for text in schedule.get("dates", []):
        day = dt.date.fromisoformat(text)
        if day.year != year:
            continue
        timestamp = dt.datetime.combine(day, dt.time(15, 30), eastern)
        # Latest Tuesday strictly before the scheduled publication, including Monday delays.
        report = day - dt.timedelta(days=(day.weekday() - 1) % 7 or 7)
        events.append((timestamp, report.isoformat()))
    past = [event for event in events if event[0] <= now]
    future = [event for event in events if event[0] > now]
    return dict(expected=max(past)[1] if past else None,
                next=min(future)[0].isoformat() if future else None)

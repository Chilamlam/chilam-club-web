"""Main-board upward price-deviation windows; calculations are not regulatory decisions.

All functions consume only the supplied history. Official events carry both the
affected trading date and the first date on which the disclosure is available.
The pilot supports three verified, continuously traded main-board listings.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import math

RULE_START = "2026-07-06"
MAIN = dict(limit=10, ordinary=20, count=4, count_text="第四次", label="主板")
GEM = dict(limit=20, ordinary=30, count=3, count_text="第三次", label="创业板")
RULE_URLS = {
    "SZ": "https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf",
    "SH": "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml",
}
CASES = {
    "sz002242": dict(name="九阳股份", benchmark="sz399107", benchmark_name="深证A股指数", exchange="SZ",
                     start="2026-09-28", focus="2026-10-16", points=["2026-10-16", "2026-10-19", "2026-11-13", "2026-11-16"]),
    "sz002148": dict(name="北纬科技", benchmark="sz399107", benchmark_name="深证A股指数", exchange="SZ",
                     start="2026-11-11", focus="2026-11-23", points=["2026-11-23", "2026-11-24", "2026-11-25"]),
    "sh600467": dict(name="好当家", benchmark="sh000002", benchmark_name="上证A股指数", exchange="SH",
                     start="2026-11-12", focus="2026-11-24", points=["2026-11-13", "2026-11-20", "2026-11-24", "2026-11-25"]),
    "sz300703": dict(name="创源股份", benchmark="sz399102", benchmark_name="创业板综合指数", exchange="SZ",
                     start="2026-11-02", focus="2026-11-25", points=["2026-11-23", "2026-11-24", "2026-11-25"]),
}


def market_rules(symbol, name=""):
    """No silent main-board fallback for ST, IPO, STAR or Beijing listings."""
    if "ST" in name.upper() or name.startswith(("N", "C", "退")):
        raise ValueError("该证券的特殊交易规则尚未核对，暂停异动推演。")
    if symbol.startswith(("sz300", "sz301")):
        return dict(GEM, benchmark="sz399102", benchmark_name="创业板综合指数", exchange="SZ")
    if symbol.startswith(("sz000", "sz001", "sz002", "sz003")):
        return dict(MAIN, benchmark="sz399107", benchmark_name="深证A股指数", exchange="SZ")
    if symbol.startswith(("sh600", "sh601", "sh603", "sh605")):
        return dict(MAIN, benchmark="sh000002", benchmark_name="上证A股指数", exchange="SH")
    raise ValueError("当前支持普通主板与创业板股票；该证券规则尚未接入。")


def price(value):
    """Exchange price tick rounding, including cheap stocks whose limits exceed 10%."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def limit_price(previous, up=True, rules=None):
    ratio = Decimal(str((rules or MAIN)["limit"])) / 100
    return price(Decimal(str(previous)) * (1 + ratio if up else 1 - ratio))


def aligned(stock, index, calendar, asof):
    """Require an exact date join; a missing bar must not silently shorten a window."""
    if asof < RULE_START:
        raise ValueError("本版仅计算2026年7月6日及以后的规则。")

    def checked(records):
        result = {}
        for row in records:
            if row["date"] > asof:
                continue
            if row["date"] in result:
                raise ValueError("行情日期重复，无法核对窗口。")
            if not all(math.isfinite(float(row[k])) and float(row[k]) > 0 for k in ("close", "adj_close")):
                raise ValueError("收盘价格缺失或无效。")
            result[row["date"]] = row
        return result

    stocks, indexes = checked(stock), checked(index)
    if asof not in stocks or asof not in indexes:
        raise ValueError("所选日缺少个股或对应指数收盘数据。")
    first = max(min(stocks), min(indexes), RULE_START)
    dates = [d for d in sorted(set(calendar)) if first <= d <= asof]
    if not dates or dates[-1] != asof:
        raise ValueError("交易日历未覆盖所选日。")
    missing = [d for d in dates if d not in stocks or d not in indexes]
    if missing:
        # An old gap outside the complete 30-session window need not block forever.
        tail = [d for d in dates if d > missing[-1]]
        if len(tail) < 31:
            raise ValueError(f"{missing[-1]}缺少行情；停牌或缺数尚未核对，暂停计算。")
        dates = tail
    if len(dates) < 31:
        raise ValueError("至少需要31个连续交易日的个股与指数数据。")
    return [dict(stocks[d], index=float(indexes[d]["close"])) for d in dates]


def windows(rows, end, length, first=1):
    """Enumerate every eligible suffix of up to N trading days, not just N days."""
    last = rows[end]
    result = []
    for start in range(max(1, first, end-length+1), end+1):
        base = rows[start-1]
        stock_return = (last["adj_close"] / base["adj_close"] - 1) * 100
        index_return = (last["index"] / base["index"] - 1) * 100
        result.append(dict(start=rows[start]["date"], base_date=base["date"], end=last["date"],
                           length=end-start+1, base_position=start-1, stock_return=stock_return,
                           index_return=index_return, deviation=stock_return-index_return))
    return result


def strongest(rows, end, length, first=1):
    return max(windows(rows, end, length, first), key=lambda x: (x["deviation"], x["length"]))


def known_events(events, asof):
    return [e for e in events if e["published_on"] <= asof and e["end"] <= asof]


def analyse(rows, events=(), rules=None):
    """Replay ordinary price triggers without overlapping counts.

    A calculated serious trigger never invents an official reset or a monitoring
    end date. Only a sourced reset, known on that date, can reset serious windows.
    Ordinary price triggers reset their own calculated period the next session;
    their count is expressly a simulation, with announcements shown separately.
    """
    rules = rules or MAIN
    ordinary_start, serious_start = 1, 1
    triggers, history = [], []
    for end in range(1, len(rows)):
        row = rows[end]
        visible = known_events(events, row["date"])
        resets = [e for e in visible if e.get("reset_on") == row["date"] and e["kind"] == "serious"]
        if resets:
            ordinary_start = serious_start = end
            triggers = []
        ordinary = strongest(rows, end, 3, ordinary_start)
        hit = ordinary["deviation"] >= rules["ordinary"] - 1e-9
        # A negative ordinary event also resets the ordinary measurement period.
        downward = min(windows(rows, end, 3, ordinary_start), key=lambda x: x["deviation"])
        if hit:
            triggers.append(dict(end=row["date"], position=end, start=ordinary["start"]))
        count = sum(e["position"] >= max(serious_start, end-9) for e in triggers)
        ten, thirty = strongest(rows, end, 10, serious_start), strongest(rows, end, 30, serious_start)
        reasons = []
        if ten["deviation"] >= 100 - 1e-9: reasons.append("10日内累计偏离达到100%")
        if thirty["deviation"] >= 200 - 1e-9: reasons.append("30日内累计偏离达到200%")
        if count >= rules["count"]: reasons.append(f"10日内{rules['count_text']}同向价格异动")
        history.append(dict(date=row["date"], close=row["close"], ten=ten, thirty=thirty,
                            ordinary=ordinary, ordinary_hit=hit, ordinary_count=count,
                            triggers=[dict(e) for e in triggers if e["position"] >= max(serious_start, end-9)],
                            serious_reasons=reasons, official_reset=bool(resets),
                            ordinary_start=ordinary_start, serious_start=serious_start,
                            complete=end >= 30 or bool(resets)))
        if hit or downward["deviation"] <= -rules["ordinary"] + 1e-9:
            ordinary_start = end+1
        history[-1]["next_ordinary_start"] = ordinary_start
    return history


def future_dates(calendar, asof, count=5):
    dates = [d for d in sorted(set(calendar)) if d > asof][:count]
    if len(dates) < count:
        raise ValueError("交易日历未覆盖后续推演日期，暂不推演。")
    return dates


def event_expiry(calendar, event_date):
    """An event ending today is absent from the trailing 10-session count at t+10."""
    dates=sorted(set(calendar))
    if event_date not in dates:
        return None
    at=dates.index(event_date)+10
    return dates[at] if at<len(dates) else None


def next_boundaries(rows, events, next_date, index_change=0.0, rules=None):
    """Return first exchange price tick crossing each upward price condition.

    Each condition is minimized over all eligible starts after the next day's
    roll, using the assumed next index close. The unrounded boundary is retained.
    """
    rules = rules or MAIN
    last = rows[-1]
    future = dict(last, date=next_date, index=last["index"]*(1+index_change/100))
    extended = rows + [future]
    history = analyse(rows, events, rules)
    state = history[-1]
    first3, first_serious = state["next_ordinary_start"], state["serious_start"]
    if any(e.get("reset_on") == next_date for e in known_events(events, last["date"]) if e["kind"] == "serious"):
        first3 = first_serious = len(rows)
    end = len(rows)
    prior_count = sum(e["position"] >= max(first_serious, end-9) for e in state["triggers"])
    result = []
    for length, threshold, first in [(3,rules['ordinary'],first3),(10,100,first_serious),(30,200,first_serious)]:
        possibilities = []
        for w in windows(extended, end, length, first):
            base = rows[w["base_position"]]
            boundary_adj = base["adj_close"] * (1+threshold/100 + w["index_return"]/100)
            boundary_raw = boundary_adj * last["close"] / last["adj_close"]
            possibilities.append((boundary_raw, w))
        boundary, w = min(possibilities, key=lambda x: x[0])
        # Subtract numerical noise only at the sub-nanoyuan level.
        tick = float(Decimal(str(boundary-1e-10)).quantize(Decimal("0.01"), rounding=ROUND_CEILING))
        result.append(dict(rule=f"{length}日", threshold=threshold, boundary=boundary, trigger_price=tick,
                           trigger_pct=(tick/last["close"]-1)*100, start=w["start"], length=w["length"],
                           ordinary_would_be=prior_count+1 if length == 3 else None,
                           serious=length != 3 or prior_count >= rules['count']-1,
                           within_limit=limit_price(last["close"],False,rules) <= tick <= limit_price(last["close"],rules=rules)))
    return result


def project(rows, events, dates, daily_change=10.0, index_change=0.0, wait_days=0, rules=None):
    """A hypothetical path, stopped at its first serious trigger; no future bars."""
    rules = rules or MAIN
    if not -rules['limit'] <= daily_change <= rules['limit'] or not -10 <= index_change <= 10:
        raise ValueError("日涨跌幅假设超出本版支持范围。")
    cutoff = rows[-1]["date"]
    visible = known_events(events, cutoff)
    if analyse(rows, visible,rules)[-1]["serious_reasons"]:
        return []  # Crossing does not imply that tomorrow's regulatory state is known.
    working = [dict(r) for r in rows]
    output = []
    for step, date in enumerate(dates):
        previous = working[-1]
        change = 0 if step < wait_days else daily_change
        close = price(Decimal(str(previous["close"])) * (1+Decimal(str(change))/100))
        row = dict(date=date, close=close, adj_close=previous["adj_close"]*close/previous["close"],
                   index=previous["index"]*(1+index_change/100), open=close, high=close, low=close, volume=0)
        working.append(row)
        state = analyse(working, visible,rules)[-1]
        output.append(dict(date=date, close=close, change=(close/previous["close"]-1)*100,
                           ten=state["ten"]["deviation"], thirty=state["thirty"]["deviation"],
                           ordinary_count=state["ordinary_count"], ordinary_hit=state["ordinary_hit"],
                           serious_reasons=state["serious_reasons"], waiting=step < wait_days))
        if state["serious_reasons"]:
            break
    return output


def roll_schedule(rows, events, dates, rules=None):
    """Isolate window movement with stock AND index held flat, without peeking ahead."""
    last = rows[-1]
    result, working = [], [dict(r) for r in rows]
    visible = known_events(events, last["date"])
    for date in dates:
        boundaries = next_boundaries(working, visible, date,rules=rules)
        boundary10 = next(x for x in boundaries if x["rule"] == "10日")
        boundary30 = next(x for x in boundaries if x["rule"] == "30日")
        expires = {}
        active_first=analyse(working,visible,rules)[-1]['serious_start']
        for n in [10,30]:
            at = len(working)-n
            expires[n] = working[at]["date"] if at >= active_first else None
        working.append(dict(last,date=date))
        result.append(dict(date=date, expires10=expires[10], expires30=expires[30],
                           boundary10=boundary10["trigger_price"], boundary30=boundary30["trigger_price"],
                           start10=boundary10["start"], start30=boundary30["start"]))
    return result


def path_rows(rows, start, rules=None):
    result = []
    for i,r in enumerate(rows):
        if i == 0 or r["date"] < start: continue
        previous = rows[i-1]["close"]
        limit = limit_price(previous,rules=rules)
        up = abs(r["close"]-limit) < .00001
        touched = r["high"] >= limit-.00001
        result.append(dict(date=r["date"], close=r["close"], high=r["high"], low=r["low"],
                           change=(r["close"]/previous-1)*100,
                           event="收盘涨停" if up else "触板未封" if touched else "未涨停"))
    return result

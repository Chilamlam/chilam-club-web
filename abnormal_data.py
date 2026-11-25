"""Public daily bars for the three-case pilot, with atomic archival and fallback."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import math
from pathlib import Path

import requests

from abnormal_windows import CASES, aligned
from update_schedule import beijing_now

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "data/speculation/abnormal_windows.json"
EVENT_PATH = ROOT / "data/speculation/abnormal_events.json"
API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def read(path=PATH):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError, ValueError):
        return {}


def calendar_dates():
    return [dt.datetime.strptime(d,"%Y%m%d").date().isoformat()
            for d in read(ROOT / "data/trading_calendar.json").get("open_dates",[])]


def parse_bars(payload, symbol, adjusted=False):
    data = payload.get("data",{})
    if not isinstance(data,dict) or not isinstance(data.get(symbol),dict):
        raise ValueError("行情返回内容无效")
    source = data[symbol]
    raw = source.get("qfqday",source.get("day")) if adjusted else source.get("day")
    if not isinstance(raw,list) or len(raw) < 31:
        raise ValueError("历史行情不足")
    rows = []
    seen = set()
    for row in raw:
        date = dt.date.fromisoformat(str(row[0])).isoformat()
        values = list(map(float,row[1:6]))
        if date in seen or len(values) != 5 or any(not math.isfinite(v) for v in values):
            raise ValueError("行情重复或数值缺失")
        opening, close, high, low, volume = values
        if min(opening,close,high,low) <= 0 or volume < 0 or not low <= min(opening,close) <= max(opening,close) <= high:
            raise ValueError("行情价格范围异常")
        rows.append(dict(date=date,open=opening,close=close,high=high,low=low,volume=volume))
        seen.add(date)
    return sorted(rows,key=lambda r:r["date"])


def fetch_series(symbol, end):
    def request(adjustment):
        response = requests.get(API,params={"param":f"{symbol},day,,,320,{adjustment}"},
                                timeout=(5,18),headers={"User-Agent":"ChilamClub public market research"})
        response.raise_for_status()
        return parse_bars(response.json(),symbol,bool(adjustment))
    raw = request("")
    adjusted = request("qfq") if not symbol.startswith(("sz399", "sh000")) else raw
    lookup = {r["date"]:r for r in adjusted}
    result = []
    for r in raw:
        if r["date"] > end: continue
        if r["date"] not in lookup: raise ValueError("复权行情日期不齐")
        result.append(dict(r,adj_close=lookup[r["date"]]["close"]))
    if len(result) < 31: raise ValueError("已收盘历史不足")
    return result


def merge_series(old, fresh):
    if not fresh or (old and fresh[-1]["date"] < old[-1]["date"]):
        raise ValueError("行情日期回退")
    lookup = {r["date"]:r for r in fresh}
    # Rebase old adjusted prices when a newer corporate action rescales the feed.
    overlap = next((r for r in reversed(old) if r["date"] in lookup),None)
    if old and overlap is None: raise ValueError("新旧行情没有重合日期")
    factor = lookup[overlap["date"]]["adj_close"]/overlap["adj_close"] if overlap else 1
    prior = [dict(r,adj_close=r["adj_close"]*factor) for r in old if r["date"] < fresh[0]["date"]]
    return prior+fresh


def refresh(archive, calendar, now=None):
    now = now or beijing_now()
    today = now.date().isoformat()
    # This is an EOD tool. Never turn an intraday last price into a closing event.
    eligible = [d for d in calendar if d < today or (d == today and now.hour*60+now.minute >= 15*60+15)]
    if not eligible: raise ValueError("交易日历未覆盖最近收盘日")
    end = max(eligible)
    symbols = list(CASES)+sorted({m["benchmark"] for m in CASES.values()})
    entries, errors = {}, []
    def one(symbol):
        try: return symbol, fetch_series(symbol,end), None
        except (requests.RequestException,ValueError,KeyError,TypeError,IndexError) as exc:
            return symbol, None, type(exc).__name__
    with ThreadPoolExecutor(max_workers=4) as pool:
        for symbol,rows,error in pool.map(one,symbols):
            if error: errors.append(f"{symbol}：{error}")
            else: entries[symbol]=rows
    result = dict(archive,checked_at=now.isoformat(),expected_date=end,errors=errors)
    if errors:
        return result  # Keep a coherent stock/index batch, not a mix of capture dates.
    try:
        merged = {s:merge_series(archive.get("series",{}).get(s,[]),entries[s]) for s in symbols}
        for symbol,meta in CASES.items():
            aligned(merged[symbol],merged[meta["benchmark"]],calendar,end)
        result.update(version=1,series=merged,asof=end,updated_at=now.isoformat(),
                      source="腾讯财经日线；区间涨幅采用同批前复权价格",source_url=API)
    except (ValueError,KeyError,TypeError) as exc:
        result["errors"].append(str(exc))
    return result


def save(payload,path=PATH):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)

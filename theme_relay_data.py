"""Complete dated THS pools and separately validated stock/index histories."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import re
from pathlib import Path

import requests

import abnormal_data as bars
import abnormal_windows as windows
from update_schedule import beijing_now, CST
from theme_relay import stage

ROOT = Path(__file__).resolve().parent / "data/speculation"
POOL_DIR = ROOT / "relay_pools"
SERIES_PATH = ROOT / "relay_prices.json"
TOPIC_PATH = ROOT / "relay_topics.json"
API = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
FIELDS = "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004"


def parse_pool(payload, date):
    value = payload.get("data") or {}
    page = value.get("page") or {}
    if payload.get("status_code") != 0 or str(value.get("date")) != date.replace("-", ""):
        raise ValueError("涨停池日期不符，未使用错日数据。")
    info = value.get("info")
    if not isinstance(info,list) or page.get("total") != len(info) or page.get("count") != 1 or page.get("page") != 1:
        raise ValueError("涨停池未完整取得，保留原归档。")
    stocks, seen = [], set()
    for r in info:
        code = str(r.get("code", ""))
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            raise ValueError("涨停池股票代码重复或无效。")
        stamp = dt.datetime.fromtimestamp(int(r["first_limit_up_time"]),CST)
        if stamp.date().isoformat() != date:
            raise ValueError("封板时间与归档日期不符。")
        name, theme, height = r.get("name"), r.get("reason_type"), r.get("high_days")
        if any(not isinstance(s,str) or not s.strip() or "\ufffd" in s for s in (name, theme, height)):
            raise ValueError("题材文字或板数缺失，未覆盖有效归档。")
        symbol = ("sh" if code.startswith("6") else "bj" if code.startswith(("4","8","92")) else "sz")+code
        stocks.append(dict(symbol=symbol,code=code,name=name,theme=theme,height=height,stage=stage(height),
            first_time=stamp.strftime("%H:%M:%S"),open_num=r.get("open_num"),
            close=float(r["latest"]),change=float(r["change_rate"]),turnover=r.get("turnover_rate"),
            circulating_value=r.get("currency_value"),source="同花顺涨停揭秘",source_url=API))
        seen.add(code)
    return dict(date=date,stocks=stocks,source="同花顺涨停揭秘",source_url=API,complete=True)


def fetch_pool(date):
    records,total,pages=[],None,None
    for page in range(1,6):
        response=requests.get(API, params=dict(page=page,limit=200,field=FIELDS,filter="HS,GEM2STAR",
            order_field="330324",order_type="0",date=date.replace("-","")),timeout=(5,15),
            headers={"User-Agent":"Mozilla/5.0","Referer":"https://data.10jqka.com.cn/limit_up/"})
        response.raise_for_status()
        payload=json.loads(response.content)
        value=payload.get('data') or {}
        pagination=value.get('page') or {}
        n,p,current=[pagination.get(k) for k in ('total','count','page')]
        if (payload.get('status_code')!=0 or str(value.get('date'))!=date.replace('-','') or
            not isinstance(value.get('info'),list) or
            any(not isinstance(x,int) or isinstance(x,bool) for x in (n,p,current)) or
            not 1<=p<=5 or current!=page or n<0 or
            (total is not None and (total!=n or pages!=p))):
            raise ValueError('涨停池分页或日期不完整，保留原归档。')
        total,pages=n,p
        records.extend(value['info'])
        if page==pages:
            break
    return parse_pool(dict(status_code=0,data=dict(date=date.replace('-',''),info=records,
        page=dict(page=1,count=1,total=total))),date)


def available_dates():
    return sorted([p.stem for p in POOL_DIR.glob("????-??-??.json")],reverse=True)


def load_pool(date):
    return bars.read(POOL_DIR / f"{date}.json")


def load_series():
    base = bars.read().get("series",{})
    archive = bars.read(SERIES_PATH)
    return dict(archive,series={**base,**archive.get("series",{})})


def refresh_prices(archive, stocks, date):
    targets = {}
    errors = []
    for stock in stocks:
        try:
            rules=windows.market_rules(stock['symbol'],stock['name'])
            targets[stock['symbol']]=rules['benchmark']
        except ValueError:
            continue
    symbols=sorted(set(targets)|set(targets.values()))
    fetched = {}
    def fetch(symbol):
        try:
            rows=bars.fetch_series(symbol,date)
            if rows[-1]['date'] != date:
                raise ValueError('缺少所选日收盘')
            return symbol, rows, None
        except (requests.RequestException,ValueError,KeyError,TypeError,IndexError) as exc:
            return symbol,None,type(exc).__name__
    with ThreadPoolExecutor(max_workers=6) as executor:
        for symbol,rows,error in executor.map(fetch,symbols):
            if error: errors.append(f"{symbol}：{error}")
            else: fetched[symbol]=rows
    result=dict(archive,series=dict(archive.get('series',{})),errors=errors,checked_at=beijing_now().isoformat())
    for symbol,benchmark in targets.items():
        if symbol not in fetched or benchmark not in fetched: continue
        try:
            windows.aligned(fetched[symbol],fetched[benchmark],bars.calendar_dates(),date)
            # A historical request must not remove newer bars used by subsequent follow-up.
            for s in (symbol,benchmark):
                old=result['series'].get(s,[])
                if not old or old[-1]['date'] <= date:
                    result['series'][s]=bars.merge_series(old,fetched[s])
        except ValueError as exc:
            errors.append(f"{symbol}：{exc}")
    return result


def refresh():
    now=beijing_now()
    dates=[d for d in bars.calendar_dates() if d<now.date().isoformat() or
           (d==now.date().isoformat() and now.hour*60+now.minute>=15*60+15)]
    date=max(dates)
    pool=fetch_pool(date)
    pool['checked_at']=now.isoformat()
    bars.save(pool,POOL_DIR/f"{date}.json")
    # Keep yesterday's leader and original candidates available after they break a board.
    tracked={r['symbol']:r for d in available_dates()[:3] for r in load_pool(d).get('stocks',[])}
    for topic in bars.read(TOPIC_PATH).get('topics',[]):
        for d in available_dates():
            previous=next((r for r in load_pool(d).get('stocks',[]) if r['symbol']==topic['leader']),None)
            if previous:
                tracked.setdefault(previous['symbol'],previous)
                break
    archive=refresh_prices(load_series(),list(tracked.values()),date)
    bars.save(archive,SERIES_PATH)
    return pool,archive

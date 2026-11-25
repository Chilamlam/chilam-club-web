"""Public exchange share history, dated NAV, and stock financing observations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import re
import warnings

import pandas as pd
import requests

from data_freshness import norm_date
from etf_flows import POOL
from market_watch import numeric
from market_watch_data import calendar, last_close_day
from update_schedule import beijing_now

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "data/observatory/etf_flows.json"
SSE = "https://www.sse.com.cn/assortment/fund/etf/list/scale/"
SZSE = "https://www.szse.cn/market/fund/volume/etf/index.html"
NAV = "https://fundf10.eastmoney.com/"


def get(url, params=None, referer=""):
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", "Referer": referer}, timeout=(5, 20))
            r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError):
            if attempt: raise


def read():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def parse_sse(payload, date):
    records = payload.get("result") or []
    if not records or len(records) != int((payload.get("pageHelp") or {}).get("total", -1)):
        raise ValueError("上交所份额分页不完整")
    rows = []
    for r in records:
        if norm_date(r.get("STAT_DATE")) != date:
            raise ValueError("上交所份额日期不符")
        symbol = str(r.get("SEC_CODE")) + ".SH"
        shares = numeric(r.get("TOT_VOL"))
        if symbol in POOL and shares is not None and shares > 0:
            rows.append(dict(date=date, symbol=symbol, shares=shares*10000, share_source="上交所"))
    if len({r['symbol'] for r in rows}) != len(rows):
        raise ValueError("上交所份额重复")
    return rows


def sse_day(date):
    formatted = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    payload = get("https://query.sse.com.cn/commonQuery.do", dict(
        sqlId="COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", STAT_DATE=formatted,
        isPagination="true", **{"pageHelp.pageSize":10000, "pageHelp.pageNo":1}), SSE).json()
    return parse_sse(payload, date)


def parse_szse(frame, start, end):
    # The JSON report is in 万份, while its XLSX currently uses 份. Read the
    # actual header, never reuse JSON units for the download.
    units = {"基金规模(份)":1, "基金规模(万份)":10000}
    col = next((c for c in units if c in frame), None)
    if not col or not {"日期", "基金代码"} <= set(frame):
        raise ValueError("深交所份额表头或单位变化")
    rows = []
    for r in frame.to_dict("records"):
        date = norm_date(str(r['日期'])[:10])
        code = numeric(r['基金代码'])
        if not date or code is None:
            continue
        if not start <= date <= end:
            raise ValueError("深交所份额日期不符")
        symbol = f"{int(code):06d}.SZ"
        shares = numeric(str(r[col]).replace(",", ""))
        if symbol in POOL and shares is not None and shares > 0:
            rows.append(dict(date=date, symbol=symbol, shares=shares*units[col], share_source="深交所"))
    if not rows or len({(r['date'],r['symbol']) for r in rows}) != len(rows):
        raise ValueError("深交所份额缺失或重复")
    return rows


def szse_range(start, end):
    def dashed(d): return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    response = get("https://www.szse.cn/api/report/ShowReport", dict(SHOWTYPE="xlsx",
        CATALOGID="scsj_fund_jjgm", TABKEY="tab1", txtStart=dashed(start), txtEnd=dashed(end), jjlb="ETF"), SZSE)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        frame = pd.read_excel(io.BytesIO(response.content), engine="openpyxl")
    return parse_szse(frame, start, end)


def nav_history(symbol, start, end):
    result, seen = [], set()
    for page in range(1, 32):
        payload = get("https://api.fund.eastmoney.com/f10/lsjz", dict(
            fundCode=symbol[:6], pageSize=60, pageIndex=page), NAV).json()
        if payload.get("ErrCode") != 0:
            raise ValueError("净值接口失败")
        batch = (payload.get("Data") or {}).get("LSJZList") or []
        if not batch:
            break
        dates = []
        for r in batch:
            date = norm_date(r.get("FSRQ"))
            if not date or date in seen:
                raise ValueError("净值日期缺失或分页重复")
            seen.add(date); dates.append(date)
            nav = numeric(r.get("DWJZ"))
            if start <= date <= end and nav is not None and nav > 0:
                result.append(dict(symbol=symbol, date=date, nav=nav))
        # This endpoint clamps the requested 60 rows to 20. Its effective page
        # size, not our request, controls exhaustion.
        size=int(payload.get("PageSize") or len(batch))
        if min(dates) <= start or page*size >= int(payload.get("TotalCount", 0)):
            break
    else:
        raise ValueError("净值历史超过分页上限，拒绝声称完整")
    if not result:
        raise ValueError("未取得同区间净值")
    return result


def split_history(symbol, cutoff):
    response = get(f"{NAV}fhsp_{symbol[:6]}.html", referer=NAV)
    # Parsing tables does not execute provider JavaScript.
    tables = pd.read_html(io.StringIO(response.text))
    for table in tables:
        if "拆分折算日" in table:
            text = table.to_string(index=False)
            if "暂无拆分信息" in text:
                return dict(status="ok", dates=[], checked_at=beijing_now().isoformat())
            dates = [norm_date(str(d)) for d in table['拆分折算日']]
            if not dates or any(not d for d in dates):
                raise ValueError("拆分日期无效")
            return dict(status="ok", dates=sorted(d for d in dates if d <= cutoff), checked_at=beijing_now().isoformat())
    raise ValueError("未取得拆分核对表")


def margin_day(day, industries):
    raw = []
    for page in range(1, 21):
        result = get("https://datacenter-web.eastmoney.com/api/data/v1/get", dict(
            reportName="RPTA_WEB_RZRQ_GGMX", columns="DATE,SECUCODE,RZYE", filter=f"(DATE='{day[:4]}-{day[4:6]}-{day[6:]}')",
            pageSize=5000, pageNumber=page, sortColumns="SECUCODE", sortTypes="1"), "https://data.eastmoney.com/rzrq/").json().get("result")
        if not result or not result.get("data"):
            raise ValueError("融资明细缺失")
        raw.extend(result['data'])
        if page >= int(result['pages']):
            break
    if len(raw) != int(result['count']) or len({r['SECUCODE'] for r in raw}) != len(raw):
        raise ValueError("融资明细分页不完整或重复")
    grouped = {}
    for r in raw:
        if norm_date(str(r['DATE'])[:10]) != day:
            raise ValueError("融资日期不符")
        code = r['SECUCODE']
        if not re.fullmatch(r"(?:6[08]\d{4}\.SH|[03]\d{5}\.SZ)", code):
            continue  # Stocks only; excludes ETFs, bonds and BSE.
        balance = numeric(r['RZYE'])
        if balance is None or balance < 0:
            raise ValueError("融资余额无效")
        name = industries.get(code) or "未分类"
        bucket = grouped.setdefault(name, dict(date=day, industry=name, balance_yi=0., count=0))
        bucket['balance_yi'] += balance/1e8
        bucket['count'] += 1
    return list(grouped.values())


def collect(backfill=0):
    old = read()
    cutoff = last_close_day()
    days = [d for d in calendar().get('open_dates', []) if d <= cutoff]
    count = min(backfill, 400) if backfill else 261 if not old.get('rows') else 6
    days = days[-max(2,count):]
    if not days:
        raise ValueError("交易日历缺失")
    errors, shares, navs, splits, margin = [], [], [], dict(old.get('splits') or {}), []
    def safe(label, fn, *args):
        try:
            return fn(*args), None
        except Exception as exc:
            return [], f"{label}：{type(exc).__name__}"
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows, error in pool.map(lambda d:safe('沪市份额 '+d,sse_day,d), days):
            shares.extend(rows)
            if error: errors.append(error)
    print(f"沪市份额：{len(shares)} 条", flush=True)
    for offset in range(0,len(days),60):
        chunk = days[offset:offset+60]
        rows,error = safe('深市份额 '+chunk[0]+'—'+chunk[-1],szse_range,chunk[0],chunk[-1])
        shares.extend(rows)
        if error: errors.append(error)
    print(f"沪深份额：{len(shares)} 条", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows,error in pool.map(lambda code:safe('净值 '+code,nav_history,code,days[0],cutoff), POOL):
            navs.extend(rows)
            if error: errors.append(error)
        for code, (value,error) in zip(POOL,pool.map(lambda code:safe('份额折算 '+code,split_history,code,cutoff), POOL)):
            # Old successful checks cannot certify newly collected sessions.
            splits[code] = value if not error else dict(status="unavailable",dates=(splits.get(code) or {}).get('dates',[]))
            if error: errors.append(error)
    print(f"同日净值：{len(navs)} 条", flush=True)
    mapping = old.get('industry_map')
    mapping_date = old.get('industry_date')
    if not mapping:
        stock = pd.read_csv(ROOT/'data/market_snapshot.csv',dtype={'ts_code':str,'date':str})
        mapping = {r.ts_code:r.industry for r in stock.itertuples() if isinstance(r.industry,str) and r.industry}
        mapping_date = max(stock.date.map(norm_date))
    margin_days = days[-min(65,len(days)):]
    # Revisit recent holes caused by transient failures; don't abandon a
    # missing day forever just because the normal incremental window moved on.
    known_margin={r['date'] for r in old.get('margin',[])}
    recent=[d for d in calendar().get('open_dates',[]) if d<=cutoff][-65:]
    margin_days=sorted(set(margin_days) | {d for d in recent if d not in known_margin})
    with ThreadPoolExecutor(max_workers=3) as pool:
        for rows,error in pool.map(lambda day:safe('融资明细 '+day,margin_day,day,mapping), margin_days):
            margin.extend(rows)
            if error: errors.append(error)
    merged = {(r['date'],r['symbol']):r for r in old.get('rows',[])}
    for r in shares:
        key=(r['date'],r['symbol'])
        merged[key]=dict(merged.get(key,dict(nav=None)),**r)
    for r in navs:
        key=(r['date'],r['symbol'])
        if key in merged: merged[key]['nav']=r['nav']
    margins = {(r['date'],r['industry']):r for r in old.get('margin',[])}
    # Replace each successfully retrieved full daily cross-section atomically.
    fresh_days={r['date'] for r in margin}
    margins={k:v for k,v in margins.items() if k[0] not in fresh_days}
    margins.update({(r['date'],r['industry']):r for r in margin})
    return dict(version=1,collected_at=beijing_now().isoformat(),cutoff=cutoff,
        rows=[v for k,v in sorted(merged.items())],splits=splits,
        margin=[v for k,v in sorted(margins.items())],industry_map=mapping,industry_date=mapping_date,
        pool=POOL,errors=errors,status="partial" if errors else "ok",
        source="份额：上交所/深交所；净值及拆分：天天基金；个股融资：东方财富交易所明细")


def publish(payload):
    if not payload.get('rows'):
        raise ValueError("未取得任何份额，保留已有文件")
    PATH.parent.mkdir(parents=True,exist_ok=True)
    tmp=PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    tmp.replace(PATH)

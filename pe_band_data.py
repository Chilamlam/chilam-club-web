"""Bounded public-source adapters; historical revisions are not point-in-time data."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import re

import requests

import pe_band as model


_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}


def _get(url, params=None):
    reply = requests.get(url, params=params, headers=_HEADERS, timeout=(5, 20))
    reply.raise_for_status()
    return reply


def parse_eastmoney(payload: dict, tx_code: str) -> list[dict]:
    result = payload.get("result") or {}
    rows = result.get("data") or []
    expected = f"{tx_code[2:]}.{tx_code[:2].upper()}"
    output = []
    for row in rows:
        if str(row.get("SECUCODE", "")).upper() != expected:
            raise ValueError("估值来源返回的交易所或股票代码不符")
        output.append(dict(date=row.get("TRADE_DATE"), close=row.get("CLOSE_PRICE"), pe_ttm=row.get("PE_TTM")))
    return output


def fetch_a_history(tx_code: str, start: dt.date, end: dt.date) -> list[dict]:
    params = dict(reportName="RPT_VALUEANALYSIS_DET", columns="ALL", pageSize=500, pageNumber=1,
                  sortColumns="TRADE_DATE", sortTypes=-1,
                  filter=f'(SECUCODE="{tx_code[2:]}.{tx_code[:2].upper()}")(TRADE_DATE>=\'{start}\')(TRADE_DATE<=\'{end}\')')
    output = []
    for page in range(1, 9):
        params["pageNumber"] = page
        payload = _get("https://datacenter-web.eastmoney.com/api/data/v1/get", params).json()
        result = payload.get("result") or {}
        rows = parse_eastmoney(payload, tx_code)
        if not rows:
            if page == 1:
                raise ValueError("该 A 股暂未取得历史 PE(TTM) 数据")
            raise ValueError("估值历史分页缺失，请稍后刷新")
        output.extend(rows)
        if page >= int(result.get("pages", 1)):
            return output
    raise ValueError("估值历史分页超出预期，暂不展示不完整结果")


def parse_baidu(payload: dict, code: str, period: str) -> list[dict]:
    try:
        result = payload["Result"][0]["DisplayData"]["resultData"]["tplData"]["result"]
        if str(result["code"]).upper() != code.upper():
            raise ValueError("估值来源返回的股票代码不符")
        charts = [c for c in result["chartInfo"] if c.get("type") == period and "市盈率(TTM)" in c.get("header", [])]
        chart = charts[0]
        dimensions = chart["dimensions"]
        date_idx, pe_idx = dimensions.index("date"), dimensions.index("curTagValue")
        rows = [dict(date=row[date_idx], pe_ttm=row[pe_idx]) for row in chart["body"]]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("该股票暂未取得所选年限的历史 PE(TTM)") from exc
    if not rows:
        raise ValueError("该股票的历史 PE(TTM) 为空")
    return rows


def fetch_global_pe(market: str, code: str, years: int) -> list[dict]:
    period = {1: "近一年", 3: "近三年", 5: "近五年"}[years]
    params = dict(openapi=1, dspName="iphone", tn="tangram", client="app", query="市盈率(TTM)",
                  code=code, word="", resource_id=51171, market="hk" if market == "HK_SHARE" else "us",
                  tag="市盈率(TTM)", chart_select=period, industry_select="", skip_industry=1, finClientType="pc")
    return parse_baidu(_get("https://gushitong.baidu.com/opendata", params).json(), code, period)


def decode_sina(text: str, code: str, market: str) -> list[dict]:
    """Decode only a quoted payload with the installed decoder, never remote JS."""
    from akshare.stock.cons import hk_js_decode, zh_js_decode
    from py_mini_racer import MiniRacer

    match = re.fullmatch(r'\s*var\s+KLC_K2_([A-Za-z0-9_.-]+)\s*=\s*"([A-Za-z0-9+/=]+)"\s*;?\s*(?:/\*.*?\*/\s*)?', text, re.S)
    if not match or match[1].upper() != code.upper():
        raise ValueError("历史价格来源的代码或格式不符")
    with MiniRacer() as js:
        js.eval(hk_js_decode if market == "HK_SHARE" else zh_js_decode)
        rows = js.call("d", match[2], timeout_sec=5, max_memory=100_000_000)
    if not isinstance(rows, list) or not rows:
        raise ValueError("历史收盘价为空")
    return [dict(date=r.get("date"), close=r.get("close")) for r in rows]


def fetch_global_prices(market: str, code: str) -> list[dict]:
    url = (f"https://finance.sina.com.cn/stock/hkstock/{code}/klc2_kl.js" if market == "HK_SHARE"
           else f"https://finance.sina.com.cn/staticdata/us/{code}")
    reply = _get(url)
    if len(reply.content) > 5_000_000:
        raise ValueError("价格来源返回异常大小的数据")
    return decode_sina(reply.text, code, market)


def fetch_history(sym: dict, years: int, *, now: dt.datetime | None = None, name: str = "") -> dict:
    market, code = model.equity_identity(sym, name)
    stamp = now or dt.datetime.now(dt.timezone.utc)
    end = model.completed_cutoff(market, stamp)
    start = model.window_start(end, years)
    if market in ("A_SHARE", "BJ_SHARE"):
        rows = fetch_a_history(sym["tx_code"], start, end)
        prices, valuations = rows, rows
        source = "东方财富：收盘价与 PE(TTM)"
        links = {"东方财富估值历史": f"https://data.eastmoney.com/gzfx/detail/{code}.html"}
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pe_future = pool.submit(fetch_global_pe, market, code, years)
            price_future = pool.submit(fetch_global_prices, market, code)
            valuations, prices = pe_future.result(), price_future.result()
        source = "百度股市通：PE(TTM)；新浪财经：未复权收盘价"
        prefix = "hk" if market == "HK_SHARE" else "us"
        links = {"百度股市通估值": f"https://finance.baidu.com/stock/{prefix}-{code}",
                 "新浪历史行情": (f"https://stock.finance.sina.com.cn/hkstock/quotes/{code}.html" if prefix == "hk"
                                  else f"https://stock.finance.sina.com.cn/usstock/quotes/{code}.html")}
    frame, coverage = model.align_history(prices, valuations, start=start, end=end)
    if frame.empty:
        raise ValueError("所选区间没有可用的历史收盘价")
    return dict(frame=frame, coverage=coverage, currency=model.CURRENCIES[market], source=source, links=links,
                retrieved_at=stamp.isoformat(), requested_start=start.isoformat(), cutoff=end.isoformat(),
                market=market, code=code, price_basis="未复权收盘价")

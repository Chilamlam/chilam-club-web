"""Nasdaq public option-chain adapter. No authenticated feed or quote-time claims."""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

import requests

from us_options import UNIVERSE, contract_id, numeric

NY = ZoneInfo("America/New_York")
ENDPOINT = "https://api.nasdaq.com/api/quote/{symbol}/option-chain"


def parse_nasdaq(payload, symbol, *, retrieved_at):
    if symbol not in UNIVERSE:
        raise ValueError("不支持的 ETF。")
    data = payload.get("data") or {}
    table = data.get("table") or {}
    rows = table.get("rows") or []
    last = str(data.get("lastTrade") or "")
    match = re.search(r"\$([\d,.]+).*?AS OF\s+([A-Z]{3}\s+\d{1,2},\s+\d{4})", last, re.I)
    if not match or not numeric(match[1]) or not rows:
        raise ValueError("来源缺少标的价格、日期或期权链。")
    asof = dt.datetime.strptime(match[2], "%b %d, %Y").date().isoformat()
    stamp = dt.datetime.fromisoformat(retrieved_at)
    if stamp.tzinfo is None or dt.date.fromisoformat(asof) > stamp.astimezone(NY).date():
        raise ValueError("来源日期在未来或抓取时间缺少时区。")
    options, seen = [], set()
    for row in rows:
        strike = numeric(row.get("strike"))
        # OSI-style URL also supplies the year. Reject adjusted roots and bad strikes.
        code = re.search(r"/([a-zA-Z0-9]+)-+(\d{6})[cp](\d{8})$", str(row.get("drillDownURL") or ""))
        if not code or code[1].upper() != symbol or not strike or abs(int(code[3])/1000 - strike) > 1e-6:
            continue
        try:
            expiry = dt.datetime.strptime(code[2], "%y%m%d").date().isoformat()
        except ValueError:
            continue
        for prefix, kind in (("c", "C"), ("p", "P")):
            ident = contract_id(symbol, expiry, kind, strike)
            if ident in seen:
                raise ValueError("来源返回重复合约，请刷新后核对。")
            seen.add(ident)
            options.append(dict(id=ident, expiry=expiry, kind=kind, strike=strike, multiplier=100,
                bid=numeric(row.get(prefix+"_Bid")), ask=numeric(row.get(prefix+"_Ask")),
                volume=numeric(row.get(prefix+"_Volume")), open_interest=numeric(row.get(prefix+"_Openinterest"))))
    if not options:
        raise ValueError("没有可识别的标准 ETF 期权合约。")
    return dict(symbol=symbol, spot=numeric(match[1]), underlying_asof=asof,
                retrieved_at=retrieved_at, quote_time=None, quote_mode="public_reference",
                source=f"https://www.nasdaq.com/market-activity/etf/{symbol.lower()}/option-chain",
                options=options)


def fetch_chain(symbol, start, end):
    if symbol not in UNIVERSE:
        raise ValueError("不支持的 ETF。")
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first > last or (last - first).days > 100:
        raise ValueError("期权链查询范围须在 100 天以内。")
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    payload, rows, offset = None, [], 0
    for _ in range(4):
        r = requests.get(ENDPOINT.format(symbol=symbol), params={"assetclass": "etf", "money": "all",
            "limit": 5000, "offset": offset, "fromdate": start, "todate": end}, headers=headers, timeout=(4, 12))
        r.raise_for_status()
        part = r.json()
        data = part.get("data") or {}
        page = (data.get("table") or {}).get("rows") or []
        total = numeric(data.get("totalRecord"))
        if total is None or not page:
            raise ValueError("来源暂未提供完整期权链。")
        if payload is None:
            payload = part
        elif data.get("lastTrade") != payload["data"].get("lastTrade"):
            raise ValueError("分页期间标的价格已变化，请刷新重新读取。")
        rows.extend(page)
        offset += len(page)
        if offset >= total:
            payload["data"]["table"]["rows"] = rows
            return parse_nasdaq(payload, symbol, retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat())
    raise ValueError("期权链超过本页范围，请缩短查询月份。")

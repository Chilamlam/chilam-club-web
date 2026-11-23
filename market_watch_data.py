"""Public read-only data adapters. Never substitute a retrieval time for a trade date."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import io
import json
from pathlib import Path
import re
import threading
import zipfile

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from data_freshness import norm_date
from market_watch import INDEXES, ETF_POOL, ETF_NAMES, basis_rows, numeric
from update_schedule import beijing_now

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "observatory"
_decoder_lock = globals().get("_decoder_lock") or threading.RLock()


def request(url, *, params=None, payload=None):
    with requests.Session() as session:
        adapter = HTTPAdapter(max_retries=Retry(total=1, backoff_factor=.2,
            status_forcelist=[429,502,503,504], allowed_methods={"GET", "POST"}))
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": "Mozilla/5.0 ChilamMarketResearch/1.0"})
        r = session.get(url, params=params, timeout=(5, 15)) if payload is None else session.post(url, json=payload, timeout=(5, 15))
        r.raise_for_status()
        return r


def bundle(rows, source, errors=(), **extra):
    return dict(version=1, collected_at=beijing_now().isoformat(), source=source,
                status="partial" if rows and errors else "ok" if rows else "unavailable",
                rows=rows, errors=list(errors), **extra)


def calendar():
    try:
        return json.loads((ROOT / "data/trading_calendar.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def last_close_day(now=None):
    now = now or beijing_now()
    days = [d for d in calendar().get("open_dates", []) if d < now.strftime("%Y%m%d")
            or (d == now.strftime("%Y%m%d") and now.hour >= 16)]
    return max(days) if days else ""


def kline(secid, *, count=130, end=None):
    data = request("https://push2his.eastmoney.com/api/qt/stock/kline/get", params={
        "secid":secid, "klt":101, "fqt":0, "lmt":count, "end":end or last_close_day(),
        "fields1":"f1,f2,f3,f4,f5,f6", "fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"}).json().get("data")
    if not isinstance(data, dict) or not data.get("klines"):
        raise ValueError("缺少日线")
    rows = []
    for line in data["klines"]:
        p = line.split(",")
        if len(p) < 9 or not norm_date(p[0]):
            continue
        rows.append(dict(date=norm_date(p[0]), close=numeric(p[2]), amount_yi=(numeric(p[6])/1e8 if numeric(p[6]) is not None else None),
                         change_pct=numeric(p[8]), name=str(data.get("name") or data.get("code"))))
    return rows


def index_kline(prefix):
    try:
        return kline(INDEXES[prefix]["secid"])
    except (requests.RequestException, ValueError):
        # Tencent's dated index daily closes are a separate, explicit fallback.
        symbol = "sh" + INDEXES[prefix]["secid"].split(".")[1]
        data = request("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", params={"param":f"{symbol},day,,,140,qfq"}).json()
        rows = (data.get("data", {}).get(symbol) or {}).get("day") or []
        if not rows:
            raise ValueError("指数日线不可用")
        return [dict(date=norm_date(p[0]), close=numeric(p[2])) for p in rows if len(p) >= 3]


def sina_etf_kline(code, cutoff):
    # Decode data using the packaged decoder, never evaluate downloaded JavaScript.
    from akshare.stock.cons import hk_js_decode
    from py_mini_racer import MiniRacer
    symbol = ("sh" if code.endswith(".SH") else "sz") + code[:6]
    raw = request(f"https://finance.sina.com.cn/realstock/company/{symbol}/hisdata_klc2/klc_kl.js").text
    match = re.search(r'=\s*"([A-Za-z0-9+/=]+)"\s*;', raw)
    if not match:
        raise ValueError("新浪日线编码格式异常")
    # V8's first initialization is process-global and cannot race across fetches.
    with _decoder_lock:
        with MiniRacer() as decoder:
            decoder.eval(hk_js_decode, timeout=5000)
            decoded = decoder.call("d", match[1], timeout=10000)
    rows = []
    for r in decoded:
        day, close, amount = norm_date(str(r.get("date", ""))[:10]), numeric(r.get("close")), numeric(r.get("amount"))
        if day and day <= cutoff and close and close > 0 and amount is not None and amount >= 0:
            rows.append(dict(date=day, close=close, amount_yi=amount/1e8,
                             name=ETF_NAMES[code], source="新浪日K"))
    if not rows:
        raise ValueError("没有包含成交额的ETF日线")
    return sorted(rows, key=lambda r:r["date"])[-131:]


def collect_etf():
    cutoff = last_close_day()
    if not cutoff:
        return bundle([], "东方财富日K", ["交易日历未覆盖，无法确定已收盘日期"])
    def one(item):
        code, index = item
        try:
            secid = ("1." if code.endswith(".SH") else "0.") + code[:6]
            try:
                history = [dict(r, source="东方财富日K") for r in kline(secid, count=131, end=cutoff)]
            except (requests.RequestException, ValueError):
                history = sina_etf_kline(code, cutoff)
            rows = [dict(r, symbol=code, index=index) for r in history if r["date"] <= cutoff]
            rows.sort(key=lambda r:r["date"])
            previous = None
            for row in rows:
                close = numeric(row.get("close"))
                row["change_pct"] = (close/previous-1)*100 if close and previous else None
                previous = close if close and close > 0 else None
            if not rows:
                raise ValueError("日线为空")
            return rows, None
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return [], f"{code}：{type(exc).__name__}，本次日线未取得"
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for group, error in pool.map(one, ETF_POOL.items()):
            rows.extend(group)
            if error:
                errors.append(error)
    return bundle(rows, "东方财富日K；缺失时用新浪日K（整段替换）：未复权价格、成交额由元换算亿元", errors, expected_symbols=list(ETF_POOL), cutoff=cutoff)


def parse_cffex_archive(content, cutoff):
    rows=[]
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r"\d{8}_1\.csv", name) or name[:8] > cutoff:
                continue
            frame = pd.read_csv(io.StringIO(archive.read(name).decode("gb18030")))
            frame.columns = frame.columns.str.strip()
            required = {"合约代码", "今收盘", "持仓量"}
            if not required <= set(frame):
                raise ValueError("中金所文件缺少价格列")
            for row in frame.to_dict("records"):
                code = str(row["合约代码"]).strip()
                if re.fullmatch(r"(?:IF|IH|IC|IM)\d{4}", code):
                    rows.append(dict(date=name[:8], contract=code, close=numeric(row["今收盘"]), open_interest=numeric(row["持仓量"])))
    return rows


def collect_futures():
    cutoff = last_close_day()
    if not cutoff:
        return bundle([], "中金所 / 东方财富 / 腾讯", ["交易日历未覆盖"])
    anchor = dt.datetime.strptime(cutoff, "%Y%m%d").date()
    serial = anchor.year*12 + anchor.month-1
    months = [f"{(serial-i)//12:04d}{(serial-i)%12+1:02d}" for i in (2,1,0)]
    futures, indexes, errors = [], [], []
    def month(ym):
        try:
            r = request(f"http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip")
            return parse_cffex_archive(r.content, cutoff), None
        except (requests.RequestException, ValueError, KeyError, zipfile.BadZipFile) as exc:
            return [], f"{ym} 中金所日行情未取得（{type(exc).__name__}）"
    with ThreadPoolExecutor(max_workers=3) as pool:
        for group, error in pool.map(month, months):
            futures.extend(group)
            if error:
                errors.append(error)
    def index(prefix):
        try:
            return [dict(r, prefix=prefix) for r in index_kline(prefix) if r["date"] <= cutoff], None
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return [], f"{INDEXES[prefix]['name']} 指数日线未取得（{type(exc).__name__}）"
    with ThreadPoolExecutor(max_workers=4) as pool:
        for group, error in pool.map(index, INDEXES):
            indexes.extend(group)
            if error:
                errors.append(error)
    rows = basis_rows(futures, indexes, calendar())
    if futures and not rows:
        errors.append("期货与现货没有同日有效收盘价，未拼接计算")
    return bundle(rows, "期货：中金所每日收盘；指数：东方财富日K，失败时腾讯日K", errors, cutoff=cutoff)


def read_archive(kind):
    try:
        return json.loads((OUT / f"{kind}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def publish(kind, data):
    """Preserve the last usable snapshot on outage; expose the failed attempt separately."""
    OUT.mkdir(parents=True, exist_ok=True)
    old = read_archive(kind)
    if not data.get("rows"):
        if old.get("rows"):
            old["last_attempt"] = {k:data.get(k) for k in ("collected_at", "status", "errors")}
            data = old
    elif kind in ("etf", "futures") and old.get("rows"):
        identity = "symbol" if kind == "etf" else "contract"
        # A partial provider failure must not discard unaffected historical series.
        merged = {(r["date"], r[identity]):r for r in old["rows"]}
        merged.update({(r["date"], r[identity]):r for r in data["rows"]})
        dates = sorted({key[0] for key in merged})[-260:]
        data = dict(data, rows=[r for key,r in sorted(merged.items()) if key[0] >= dates[0]])
    target = OUT / f"{kind}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    tmp.replace(target)

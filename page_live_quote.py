"""
全市场实时行情与多周期 K 线看板 (A股/ETF/指数 · 港股 · 美股 · 大宗商品)

数据通道均为实测可用的公开接口，并且**每个市场只暴露真正取得到数据的周期**：

| 市场      | 报价 | 分时 | 分钟K(5/15/30/60) | 日/周/月K |
|-----------|------|------|-------------------|-----------|
| A股/ETF/指数 | 腾讯 | 腾讯 | 腾讯 mkline        | 腾讯 fqkline |
| 港股      | 腾讯 | 腾讯 | ❌ 无公开源        | 腾讯 hkfqkline |
| 美股      | 腾讯 | 新浪 | 新浪 US_MinKService | 腾讯 usfqkline (需 .OQ/.N 后缀) |
| 大宗商品  | 新浪 | 新浪 | ❌ 无公开源        | 新浪日K(周/月由日K聚合) |

页面上的周期选项按上表动态生成，不会再出现「能选但没数据」的情况。

两个容易踩的实测细节：
1. 腾讯 `_TX_PERIOD` 里 "month" 也以 m 开头，判断分钟线必须白名单 m5/m15/m30/m60，
   否则月 K 会被误发到 mkline 接口而返回空。
2. 美股指数(.IXIC/.DJI/.INX) 的分时与分钟 K 在新浪要带前导点，腾讯日/周/月 K 则用
   usIXIC 这种不带交易所后缀的写法；美股个股反过来必须带 .OQ/.N 后缀。
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import urllib.request
import json
import re

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_UA_SINA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://finance.sina.com.cn"}

# 各市场支持的周期（实测结论，改动前请先跑 tools_probe_quote_api.py 复验）
MARKET_PERIODS = {
    "A_SHARE": ["分时走势", "5分钟", "15分钟", "30分钟", "60分钟", "日K", "周K", "月K"],
    "HK_SHARE": ["分时走势", "日K", "周K", "月K"],
    "US_SHARE": ["分时走势", "5分钟", "15分钟", "30分钟", "60分钟", "日K", "周K", "月K"],
    "FUTURES": ["分时走势", "日K", "周K", "月K"],
}

MARKET_LABEL = {
    "A_SHARE": "A股 / ETF / 指数",
    "HK_SHARE": "港股",
    "US_SHARE": "美股",
    "FUTURES": "国际大宗商品",
}

# 缺失周期的说明，用于在页面上讲清楚为什么没有分钟 K
MARKET_LIMIT_NOTE = {
    "HK_SHARE": "港股暂无稳定的公开分钟级 K 线数据源，因此只提供分时与日/周/月 K。",
    "FUTURES": "国际商品期货暂无稳定的公开分钟级 K 线数据源，周 K / 月 K 由日 K 聚合而成。",
}

# 新浪支持的国际商品符号（实测有报价的）
FUTURES_SYMBOLS = {
    "GC": "纽约黄金", "SI": "纽约白银", "CL": "纽约原油", "HG": "美铜",
    "NG": "美国天然气", "C": "美国玉米", "S": "美国大豆", "W": "美国小麦",
    "CT": "美国棉花", "ES": "标普500指数期货", "NK": "日经225指数期货",
    "HSI": "恒生指数期货", "CAD": "伦铜",
}
FUTURES_ALIAS = {"GOLD": "GC", "黄金": "GC", "SILVER": "SI", "白银": "SI",
                 "OIL": "CL", "原油": "CL", "COPPER": "HG", "铜": "HG",
                 "天然气": "NG", "GAS": "NG"}

# 这些商品符号与港股指数/美股 ticker 撞名(HSI=恒生指数, C=花旗, W=Wayfair ...)，
# 裸输入时不按商品解析，必须显式写 HF_ 前缀，例如 HF_HSI = 恒指期货
_FUTURES_AMBIGUOUS = {"HSI", "C", "S", "W", "CT", "CAD"}

_PRICE_DECIMALS = {"A_SHARE": 2, "HK_SHARE": 3, "US_SHARE": 2, "FUTURES": 3}


def _http_text(url: str, headers=None, encoding="utf-8", timeout=8) -> str:
    req = urllib.request.Request(url, headers=headers or _UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, errors="ignore")


def _http_json(url: str, headers=None, timeout=10) -> dict:
    return json.loads(_http_text(url, headers=headers, timeout=timeout))


def _sina_jsonp(url: str, timeout=15):
    """新浪 jsonp 接口：剥掉 x(...) 外壳。"""
    txt = _http_text(url, headers=_UA_SINA, timeout=timeout)
    m = re.search(r"x\((.*)\)\s*;?\s*$", txt.strip(), re.S)
    return json.loads(m.group(1)) if m else json.loads(txt)


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ==================== 1. 代码解析 ====================

_HK_INDEX = {"HSI": "恒生指数", "HSCEI": "恒生中国企业指数", "HSTECH": "恒生科技指数"}
_US_INDEX = {"DJI": "道琼斯", "IXIC": "纳斯达克", "INX": "标普500"}


def resolve_symbol(raw: str) -> dict:
    """把用户输入解析为统一的标的描述。

    返回 dict:
      market   : A_SHARE / HK_SHARE / US_SHARE / FUTURES
      tx_code  : 腾讯行情代码 (商品市场为 None)
      sina_sym : 新浪符号 (美股为 ticker，商品为 GC 这类符号)
      display  : 展示用代码
    """
    s = (raw or "").strip().upper().replace(" ", "")
    if not s:
        return {}

    # 显式前缀：HF_GC / GC 商品
    if s.startswith("HF_"):
        sym = s[3:]
        return {"market": "FUTURES", "tx_code": None, "sina_sym": sym, "display": sym}
    if s in FUTURES_ALIAS:
        sym = FUTURES_ALIAS[s]
        return {"market": "FUTURES", "tx_code": None, "sina_sym": sym, "display": sym}
    if s in FUTURES_SYMBOLS and s not in _FUTURES_AMBIGUOUS:
        return {"market": "FUTURES", "tx_code": None, "sina_sym": s, "display": s}

    # A 股 / ETF / 指数：6 位数字
    if re.fullmatch(r"\d{6}", s):
        # 沪市: 6xxxxx 主板/688 科创、5xxxxx 基金、11xxxx 转债、000xxx & 999xxx 上证指数
        sh = s.startswith(("60", "68", "51", "58", "56", "50", "11", "000", "999"))
        prefix = "sh" if sh else "sz"
        return {"market": "A_SHARE", "tx_code": f"{prefix}{s}", "sina_sym": None, "display": s}

    if re.fullmatch(r"(SH|SZ)\d{6}", s):
        return {"market": "A_SHARE", "tx_code": s.lower(), "sina_sym": None, "display": s}

    # 港股：3~5 位数字 或 HK 前缀
    if re.fullmatch(r"\d{3,5}", s):
        code = s.zfill(5)
        return {"market": "HK_SHARE", "tx_code": f"hk{code}", "sina_sym": code, "display": code}
    if re.fullmatch(r"HK\d{3,5}", s):
        code = s[2:].zfill(5)
        return {"market": "HK_SHARE", "tx_code": f"hk{code}", "sina_sym": code, "display": code}

    # 港股指数
    if s in _HK_INDEX:
        return {"market": "HK_SHARE", "tx_code": f"hk{s}", "sina_sym": None, "display": s}
    # 美股指数：新浪分时/分钟K 需要带前导点的符号 (.IXIC)，腾讯日/周/月 K 用 usIXIC
    if s.lstrip(".") in _US_INDEX:
        t = s.lstrip(".")
        return {"market": "US_SHARE", "tx_code": f"us{t}", "sina_sym": f".{t}",
                "display": f".{t}", "is_index": True}

    # 美股：纯字母(可带 . 分隔)
    if re.fullmatch(r"[A-Z][A-Z\.\-]{0,7}", s):
        ticker = s.split(".")[0]
        return {"market": "US_SHARE", "tx_code": f"us{ticker}", "sina_sym": ticker, "display": ticker}

    return {}


# ==================== 2. 实时报价 ====================

def _parse_tx_quote(parts: list, market: str) -> dict:
    """腾讯行情 ~ 分隔字段解析。A股 88 段 / 港股 78 段 / 美股 71 段，
    前 6 段与 30~45 段语义一致，可统一取值。"""
    if len(parts) < 40:
        return {}
    return {
        "name": parts[1],
        "code": parts[2],
        "price": _f(parts[3]),
        "last_close": _f(parts[4]),
        "open": _f(parts[5]),
        "chg": _f(parts[31]),
        "pct_chg": _f(parts[32]),
        "high": _f(parts[33]),
        "low": _f(parts[34]),
        "amount": _f(parts[37]),          # A股单位: 万元; 港股/美股: 元
        "amount_unit": "万元" if market == "A_SHARE" else "元",
        "pe": _f(parts[39]),
        "mv_yi": _f(parts[45]) if len(parts) > 45 else 0.0,
        "update_time": parts[30],
    }


def _fetch_tx_quote(tx_code: str, market: str) -> dict:
    try:
        txt = _http_text(f"https://qt.gtimg.cn/q={tx_code}", encoding="gbk")
        m = re.search(r'="([^"]*)"', txt)
        if not m or not m.group(1):
            return {}
        return _parse_tx_quote(m.group(1).split("~"), market)
    except Exception:
        return {}


def _fetch_futures_quote(sym: str) -> dict:
    """新浪国际期货报价。字段: 0现价 2买 3卖 4最高 5最低 6时间 7昨收 8今开 12日期 13名称"""
    try:
        txt = _http_text(f"https://hq.sinajs.cn/list=hf_{sym}", headers=_UA_SINA, encoding="gbk")
        m = re.search(r'="([^"]*)"', txt)
        if not m or not m.group(1):
            return {}
        p = m.group(1).split(",")
        if len(p) < 14:
            return {}
        price, last_close = _f(p[0]), _f(p[7])
        pct = (price - last_close) / last_close * 100 if last_close else 0.0
        return {
            "name": p[13] or FUTURES_SYMBOLS.get(sym, sym),
            "code": sym,
            "price": price,
            "last_close": last_close,
            "open": _f(p[8]),
            "chg": price - last_close,
            "pct_chg": pct,
            "high": _f(p[4]),
            "low": _f(p[5]),
            "amount": 0.0,
            "amount_unit": "",
            "pe": 0.0,
            "mv_yi": 0.0,
            "update_time": f"{p[12]} {p[6]}",
        }
    except Exception:
        return {}


@st.cache_data(ttl=8, show_spinner=False)
def get_quote(sym: dict) -> dict:
    market = sym.get("market")
    if market == "FUTURES":
        return _fetch_futures_quote(sym["sina_sym"])

    q = _fetch_tx_quote(sym["tx_code"], market)
    # 6 位数字里 000xxx/300xxx 存在沪深前缀歧义，另一个前缀兜底
    if not q and market == "A_SHARE":
        code = sym["tx_code"]
        alt = ("sz" + code[2:]) if code.startswith("sh") else ("sh" + code[2:])
        q = _fetch_tx_quote(alt, market)
        if q:
            sym["tx_code"] = alt
    return q


# ==================== 3. 分时走势 ====================

@st.cache_data(ttl=20, show_spinner=False)
def get_minute_line(sym: dict) -> pd.DataFrame:
    """当日分时。A股/港股走腾讯，美股走新浪(腾讯只返回收盘一个点)，商品走新浪全球期货。"""
    market = sym.get("market")
    try:
        if market in ("A_SHARE", "HK_SHARE"):
            code = sym["tx_code"]
            d = _http_json(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}")
            raw = (((d.get("data") or {}).get(code) or {}).get("data") or {}).get("data") or []
            rows = []
            for item in raw:
                p = item.split()
                if len(p) >= 2 and len(p[0]) == 4:
                    rows.append({"time": f"{p[0][:2]}:{p[0][2:]}",
                                 "price": _f(p[1]),
                                 "vol": _f(p[2]) if len(p) > 2 else 0.0})
            return pd.DataFrame(rows)

        if market == "US_SHARE":
            ticker = sym.get("sina_sym")
            if not ticker:
                return pd.DataFrame()
            d = _http_json(
                "https://stock.finance.sina.com.cn/usstock/api/json_v2.php/"
                f"US_MinlineService.getMinline?symbol={ticker}", headers=_UA_SINA, timeout=15)
            arr = d.get("minline_1") or []
            if not arr:
                return pd.DataFrame()
            blk = arr[0]
            fm = blk.get("first_min") or []
            others = blk.get("other_min") or []
            if len(fm) < 5:
                return pd.DataFrame()
            # first_min = [日期, 起始时间, 开盘, 首分钟收盘, 首分钟量]
            rows = [{"time": fm[1][:5], "price": _f(fm[3]), "vol": _f(fm[4])}]
            hh, mm = int(fm[1][:2]), int(fm[1][3:5])
            for it in others:
                mm += 1
                if mm >= 60:
                    hh, mm = hh + 1, 0
                rows.append({"time": f"{hh % 24:02d}:{mm:02d}",
                             "price": _f(it[0]),
                             "vol": _f(it[1]) if len(it) > 1 else 0.0})
            return pd.DataFrame(rows)

        if market == "FUTURES":
            d = _sina_jsonp(
                "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/x/"
                f"GlobalFuturesService.getGlobalFuturesMinLine?symbol={sym['sina_sym']}")
            raw = d.get("minLine_1d") or []
            rows = []
            for it in raw:
                # 首行 10 段(含日期/昨收/交易所)，其余 6 段: [时间, 价, ?, ?, 均价, 完整时间]
                if len(it) >= 10:
                    rows.append({"time": it[4], "price": _f(it[5]), "vol": 0.0})
                elif len(it) >= 5:
                    rows.append({"time": it[0], "price": _f(it[1]), "vol": 0.0})
            return pd.DataFrame(rows)
    except Exception:
        pass
    return pd.DataFrame()


# ==================== 4. K 线 ====================

_TX_PERIOD = {"5分钟": "m5", "15分钟": "m15", "30分钟": "m30", "60分钟": "m60",
              "日K": "day", "周K": "week", "月K": "month"}
_SINA_US_MIN = {"5分钟": "5", "15分钟": "15", "30分钟": "30", "60分钟": "60"}


def _tx_kline_records(arr: list) -> pd.DataFrame:
    rows = []
    for it in arr:
        if len(it) < 6:
            continue
        d = str(it[0])
        if len(d) == 12:                       # 202608281030
            label = f"{d[4:6]}-{d[6:8]} {d[8:10]}:{d[10:12]}"
        else:
            label = d                          # 2026-08-28
        rows.append({"datetime": label, "open": _f(it[1]), "close": _f(it[2]),
                     "high": _f(it[3]), "low": _f(it[4]), "vol": _f(it[5])})
    return pd.DataFrame(rows)


def _tx_kline(code: str, period: str, market: str, limit: int = 240) -> pd.DataFrame:
    p = _TX_PERIOD.get(period)
    if not p:
        return pd.DataFrame()
    # 注意: 不能用 p.startswith("m") 判断分钟线 —— "month" 也以 m 开头
    if p in ("m5", "m15", "m30", "m60"):
        url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},{p},,{limit}"
    else:
        ep = {"A_SHARE": "fqkline", "HK_SHARE": "hkfqkline", "US_SHARE": "usfqkline"}[market]
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/{ep}/get"
               f"?param={code},{p},,,{limit},qfq")
    try:
        d = _http_json(url, timeout=12)
        node = (d.get("data") or {}).get(code) or {}
        arr = node.get(p) or node.get(f"qfq{p}") or []
        if not arr:
            # 有些标的返回 key 不带 qfq 前缀，兜底取第一个 list
            for v in node.values():
                if isinstance(v, list) and v:
                    arr = v
                    break
        return _tx_kline_records(arr)
    except Exception:
        return pd.DataFrame()


def _sina_us_minute_kline(ticker: str, period: str) -> pd.DataFrame:
    t = _SINA_US_MIN.get(period)
    if not t:
        return pd.DataFrame()
    try:
        d = _http_json("https://stock.finance.sina.com.cn/usstock/api/json_v2.php/"
                       f"US_MinKService.getMinK?symbol={ticker}&type={t}",
                       headers=_UA_SINA, timeout=15)
        rows = []
        for it in d[-240:]:
            ds = it.get("d", "")
            rows.append({"datetime": ds[5:16], "open": _f(it.get("o")),
                         "close": _f(it.get("c")), "high": _f(it.get("h")),
                         "low": _f(it.get("l")), "vol": _f(it.get("v"))})
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def _sina_futures_kline(sym: str, period: str) -> pd.DataFrame:
    """新浪只有日 K，周/月 K 在本地按自然周/月聚合。"""
    try:
        d = _sina_jsonp("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/x/"
                        f"GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={sym}")
        rows = [{"datetime": it["date"], "open": _f(it["open"]), "close": _f(it["close"]),
                 "high": _f(it["high"]), "low": _f(it["low"]), "vol": _f(it.get("volume"))}
                for it in d if it.get("date")]
        df = pd.DataFrame(rows)
        if df.empty or period == "日K":
            return df.tail(240).reset_index(drop=True)

        df["_dt"] = pd.to_datetime(df["datetime"])
        rule = "W" if period == "周K" else "ME"
        g = df.set_index("_dt").resample(rule).agg(
            {"open": "first", "close": "last", "high": "max", "low": "min", "vol": "sum"}).dropna()
        g["datetime"] = g.index.strftime("%Y-%m-%d")
        return g.reset_index(drop=True)[["datetime", "open", "close", "high", "low", "vol"]].tail(240)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60, show_spinner=False)
def get_kline(sym: dict, period: str) -> pd.DataFrame:
    market = sym.get("market")
    if market == "FUTURES":
        return _sina_futures_kline(sym["sina_sym"], period)

    if market == "US_SHARE":
        if period in _SINA_US_MIN:
            return _sina_us_minute_kline(sym["sina_sym"], period)
        # 指数(usIXIC/usDJI/usINX)不带交易所后缀，直接用 tx_code
        if sym.get("is_index"):
            return _tx_kline(sym["tx_code"], period, "US_SHARE")
        # 美股个股日/周/月 K 必须用带 .OQ / .N 交易所后缀的代码，否则只返回 1~2 根
        q = get_quote(sym)
        full = q.get("code") or ""
        code = f"us{full}" if full and "." in full else sym["tx_code"]
        df = _tx_kline(code, period, "US_SHARE")
        if df.empty and code != sym["tx_code"]:
            df = _tx_kline(sym["tx_code"], period, "US_SHARE")
        return df

    return _tx_kline(sym["tx_code"], period, market)


# ==================== 5. 交易时间轴 ====================

def _minutes(start: str, end: str) -> list:
    """生成 [start, end] 闭区间的每分钟标签，支持跨零点。"""
    sh, sm = int(start[:2]), int(start[3:5])
    eh, em = int(end[:2]), int(end[3:5])
    total_s, total_e = sh * 60 + sm, eh * 60 + em
    if total_e < total_s:
        total_e += 24 * 60
    return [f"{(t // 60) % 24:02d}:{t % 60:02d}" for t in range(total_s, total_e + 1)]


# 每个市场的交易时段（北京时间）与 X 轴主刻度
_SESSIONS = {
    "A_SHARE": ([("09:30", "11:30"), ("13:00", "15:00")],
                ["09:30", "10:30", "11:30", "14:00", "15:00"],
                ["09:30", "10:30", "11:30/13:00", "14:00", "15:00"]),
    "HK_SHARE": ([("09:30", "12:00"), ("13:00", "16:10")],
                 ["09:30", "10:30", "12:00", "14:30", "16:10"],
                 ["09:30", "10:30", "12:00/13:00", "14:30", "16:10"]),
    # 美股用当地时间轴(09:30-16:00)，与新浪分时返回的时间戳一致
    "US_SHARE": ([("09:30", "16:00")],
                 ["09:30", "11:00", "12:30", "14:00", "16:00"],
                 ["09:30", "11:00", "12:30", "14:00", "16:00"]),
    # 国际商品近乎全天，新浪返回 06:00 -> 次日 05:00
    "FUTURES": ([("06:00", "05:00")],
                ["06:00", "12:00", "18:00", "00:00", "05:00"],
                ["06:00", "12:00", "18:00", "00:00", "05:00"]),
}


def _timeline(market: str) -> tuple:
    sessions, tv, tt = _SESSIONS.get(market, ([("00:00", "23:59")], None, None))
    tl = []
    for a, b in sessions:
        tl.extend(_minutes(a, b))
    return list(dict.fromkeys(tl)), tv, tt


# ==================== 6. 页面渲染 ====================

PRESETS = [
    ("贵州茅台", "600519"), ("上证指数", "sh000001"), ("纳指ETF", "513100"),
    ("腾讯控股", "00700"), ("恒生指数", "HSI"), ("英伟达", "NVDA"),
    ("纳斯达克", "IXIC"), ("纽约黄金", "GC"),
]


def _fmt(v: float, market: str) -> str:
    return f"{v:.{_PRICE_DECIMALS.get(market, 2)}f}"


def _render_minute_chart(sym: dict, quote: dict, df: pd.DataFrame):
    market = sym["market"]
    name = quote.get("name", sym["display"])
    last_close = quote.get("last_close") or df["price"].iloc[0]

    dev = max(abs(df["price"].max() - last_close), abs(df["price"].min() - last_close))
    if dev <= 0:
        dev = max(last_close * 0.01, 0.01)
    y_min, y_max = last_close - dev * 1.15, last_close + dev * 1.15

    if df["vol"].sum() > 0:
        cum_v = df["vol"].cumsum().replace(0, pd.NA)
        df["avg_price"] = ((df["price"] * df["vol"]).cumsum() / cum_v).astype(float)
    else:
        df["avg_price"] = df["price"].expanding().mean()

    timeline, tick_vals, tick_texts = _timeline(market)
    full = pd.merge(pd.DataFrame({"time": timeline}), df, on="time", how="left")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=full["time"], y=full["price"], mode="lines", name="现价",
                             line=dict(color="#0984e3", width=2), connectgaps=False))
    fig.add_trace(go.Scatter(x=full["time"], y=full["avg_price"], mode="lines", name="均价",
                             line=dict(color="#f39c12", width=1.5, dash="dot"), connectgaps=False))
    fig.add_hline(y=last_close, line_dash="dash", line_color="rgba(128,128,128,0.7)",
                  annotation_text=f" 昨收 {_fmt(last_close, market)}",
                  annotation_position="bottom right")

    xaxis = dict(type="category", gridcolor="rgba(128,128,128,0.15)")
    if tick_vals:
        keep = [v for v in tick_vals if v in timeline]
        xaxis.update(tickmode="array", tickvals=keep,
                     ticktext=[tick_texts[tick_vals.index(v)] for v in keep])

    tz_note = "（美东时间）" if market == "US_SHARE" else ""
    fig.update_layout(
        title=f"{name} · 当日分时{tz_note}",
        xaxis=xaxis,
        yaxis=dict(title="价格", range=[y_min, y_max], autorange=False,
                   gridcolor="rgba(128,128,128,0.15)"),
        hovermode="x unified", height=470,
        legend=dict(orientation="h", y=1.08, x=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_kline_chart(sym: dict, quote: dict, df: pd.DataFrame, period: str):
    name = quote.get("name", sym["display"])
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["datetime"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=period, increasing_line_color="#ff4d4d", decreasing_line_color="#2ecc71"))
    for w, color in ((5, "#f39c12"), (20, "#3498db")):
        if len(df) >= w:
            fig.add_trace(go.Scatter(x=df["datetime"], y=df["close"].rolling(w).mean(),
                                     name=f"MA{w}", line=dict(color=color, width=1.4)))
    fig.update_layout(
        title=f"{name} · {period}（{len(df)} 根）",
        xaxis=dict(rangeslider=dict(visible=False), tickangle=-45, type="category",
                   nticks=14, gridcolor="rgba(128,128,128,0.2)"),
        yaxis=dict(title="价格", gridcolor="rgba(128,128,128,0.2)"),
        hovermode="x unified", height=520,
        legend=dict(orientation="h", y=1.06, x=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_live_quote_page():
    st.header("⚡ 全市场实时行情与 K 线看板")
    st.caption("A股 / ETF / 指数 · 港股 · 美股 · 国际大宗商品 —— 周期选项按各市场实际可取到的数据动态生成")

    if "active_symbol" not in st.session_state:
        st.session_state.active_symbol = "600519"

    st.markdown("**🔥 快速直达**")
    cols = st.columns(4)
    for i, (label, code) in enumerate(PRESETS):
        with cols[i % 4]:
            if st.button(f"{label} `{code}`", key=f"pre_{code}", use_container_width=True):
                st.session_state.active_symbol = code
                st.rerun()

    c_in, c_btn = st.columns([4, 1])
    with c_in:
        raw = st.text_input(
            "🔍 标的代码",
            value=st.session_state.active_symbol,
            help="A股/ETF/指数: 600519、000001、513100、sh000001 ｜ 港股: 00700、HSI ｜ "
                 "美股: NVDA、AAPL、IXIC、DJI ｜ 商品: GC 黄金、CL 原油、SI 白银、HG 铜、NG 天然气 ｜ "
                 "与股票撞名的商品需加 HF_ 前缀，如 HF_HSI 恒指期货、HF_C 美玉米",
        )
    with c_btn:
        st.write("")
        st.write("")
        if st.button("🔄 刷新", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    if not raw:
        st.info("请输入标的代码。")
        return

    sym = resolve_symbol(raw)
    if not sym:
        st.error(f"无法识别代码 `{raw}`。A股填 6 位数字，港股 5 位数字，美股填字母代码，商品填 GC / CL 这类符号。")
        return

    quote = get_quote(sym)
    if not quote or quote.get("price", 0) <= 0:
        st.error(f"未取到 `{raw}` 的行情。请确认代码是否正确（识别为{MARKET_LABEL[sym['market']]}）。")
        return

    market = sym["market"]
    pct = quote.get("pct_chg", 0.0)

    k = st.columns(5)
    k[0].metric("标的", quote.get("name", sym["display"]), delta=MARKET_LABEL[market],
                delta_color="off")
    k[1].metric("最新价", _fmt(quote.get("price", 0), market), delta=f"{pct:+.2f}%")
    k[2].metric("最高 / 最低",
                f"{_fmt(quote.get('high', 0), market)} / {_fmt(quote.get('low', 0), market)}")
    amt = quote.get("amount", 0.0)
    if market == "A_SHARE" and amt:
        k[3].metric("成交额", f"{amt / 10000:.2f} 亿")
    elif amt:
        k[3].metric("成交额", f"{amt / 1e8:.2f} 亿")
    else:
        k[3].metric("昨收", _fmt(quote.get("last_close", 0), market))
    k[4].metric("行情时间", str(quote.get("update_time", ""))[-8:])

    st.markdown("---")

    periods = MARKET_PERIODS[market]
    period = st.radio("📈 周期", periods, index=0, horizontal=True, key=f"period_{market}")

    note = MARKET_LIMIT_NOTE.get(market)
    if note:
        st.caption(f"ℹ️ {note}")

    if period == "分时走势":
        df = get_minute_line(sym)
        if df.empty:
            st.info("暂无当日分时数据（可能尚未开盘或数据源临时无响应），可切换到日 K 查看。")
        else:
            _render_minute_chart(sym, quote, df)
    else:
        df = get_kline(sym, period)
        if df.empty:
            st.info(f"暂未取到 {period} 数据，数据源可能临时无响应，稍后重试或换个周期。")
        else:
            _render_kline_chart(sym, quote, df, period)

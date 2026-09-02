"""
全市场实时行情与多周期 K 线看板 (A股/ETF/指数 · 北交所 · 港股 · 美股 · 大宗商品)

数据通道均为实测可用的公开接口，并且**每个市场只暴露真正取得到数据的周期**：

| 市场      | 报价 | 分时 | 分钟K(5/15/30/60) | 日/周/月K |
|-----------|------|------|-------------------|-----------|
| A股/ETF/指数 | 腾讯 | 腾讯 | 腾讯 mkline        | 腾讯 fqkline |
| 北交所    | 腾讯 | 腾讯 | ❌ mkline 返回空   | 腾讯 newfqkline (fqkline 只给 1 根!) |
| 港股      | 腾讯 | 腾讯 | ❌ 无公开源        | 腾讯 hkfqkline |
| 美股      | 腾讯 | 新浪 | 新浪 US_MinKService | 腾讯 usfqkline (需 .OQ/.N 后缀) |
| 大宗商品  | 新浪 | 新浪 | ❌ 无公开源        | 新浪日K(周/月由日K聚合) |

页面上的周期选项按上表动态生成，不会再出现「能选但没数据」的情况。

四个容易踩的实测细节：
1. 腾讯 `_TX_PERIOD` 里 "month" 也以 m 开头，判断分钟线必须白名单 m5/m15/m30/m60，
   否则月 K 会被误发到 mkline 接口而返回空。
2. 美股指数(.IXIC/.DJI/.INX) 的分时与分钟 K 在新浪要带前导点，腾讯日/周/月 K 则用
   usIXIC 这种不带交易所后缀的写法；美股个股反过来必须带 .OQ/.N 后缀。
3. 北交所日/周/月 K **必须走 newfqkline**：老的 fqkline 端点对 bj 代码只返回当天
   1 根 K 线（不报错、不为空 —— 典型静默失败），足够骗过「df 非空」这类断言。
4. 同一个 6 位代码在沪深两市常常各有标的（000831 = 沪市指数「500低贝」+ 深市个股
   「中国稀土」），且**沪市指数在未开盘时依然返回价格**，只是 amount=0、high/low=0。
   所以「取到数据」不等于「取对标的」，消歧必须看活跃度而不是看是否为空。
   详见 `resolve_candidates()`。

名称搜索是双源的（见 `_search_kernel`）：smartbox 覆盖沪深主板 / 科创 / B股 /
港美股 / ETF / 指数，但**对北交所与可转债一律返回 0 条**（中文名、拼音、纯代码
三种写法全搜不到），这块由东财 suggest 兜底。合并后统一用腾讯批量报价校验，
保证「搜出来的每一条都点得开」。

自动技术分析（K 线周期可用，分时不做结构识别）由两个模块提供：
  tech_analysis.py —— 纯计算（缠论包含处理/分型/笔/线段/中枢；帝纳波利 DMA/
                      Fibnode/COP·OP·XOP/汇聚区/MACD 8-17-9 背离），不依赖 Streamlit
  tech_overlay.py  —— 纯绘制（Plotly 图层 + 文字结论 + 免责声明）
自检: tools_probe_tech_analysis.py（结构合法性 + 全周期渲染，须用 stcheck venv 跑）
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import urllib.request
import urllib.parse
import json
import re

import tech_analysis as ta
import tech_overlay as tov

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_UA_SINA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://finance.sina.com.cn"}

# 各市场支持的周期（实测结论，改动前请先跑 tools_probe_quote_api.py 复验）
MARKET_PERIODS = {
    "A_SHARE": ["分时走势", "5分钟", "15分钟", "30分钟", "60分钟", "日K", "周K", "月K"],
    # 北交所: mkline 对 bj 代码返回 m5 键但列表恒为空，故不暴露分钟周期
    "BJ_SHARE": ["分时走势", "日K", "周K", "月K"],
    "HK_SHARE": ["分时走势", "日K", "周K", "月K"],
    "US_SHARE": ["分时走势", "5分钟", "15分钟", "30分钟", "60分钟", "日K", "周K", "月K"],
    "FUTURES": ["分时走势", "日K", "周K", "月K"],
}

MARKET_LABEL = {
    "A_SHARE": "A股 / ETF / 指数",
    "BJ_SHARE": "北交所",
    "HK_SHARE": "港股",
    "US_SHARE": "美股",
    "FUTURES": "国际大宗商品",
}

# 缺失周期的说明，用于在页面上讲清楚为什么没有分钟 K
MARKET_LIMIT_NOTE = {
    "BJ_SHARE": "北交所标的的分钟级 K 线在公开接口返回为空，因此只提供分时与日/周/月 K；"
                "部分老三板代码（430xxx / 83xxxx）连日 K 也没有历史，属数据源限制。",
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

_PRICE_DECIMALS = {"A_SHARE": 2, "BJ_SHARE": 2, "HK_SHARE": 3, "US_SHARE": 2, "FUTURES": 3}


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

_TX_PREFIX_MARKET = {"sh": "A_SHARE", "sz": "A_SHARE", "bj": "BJ_SHARE",
                     "hk": "HK_SHARE", "us": "US_SHARE"}

# 6 位代码的前缀先验。同一串数字沪深北最多同时存在三个标的，这里只决定
# 「都活跃时优先谁」，真正的取舍靠 _rank_candidates 的活跃度判据。
_PREFIX_PRIOR = [
    # (代码前缀, 按优先级排列的市场前缀)
    (("60", "68", "90", "58", "56", "51", "50", "11", "13", "20", "73"), ("sh", "sz")),
    (("999",), ("sh",)),
    (("000",), ("sh", "sz")),          # 000xxx: 上证指数系列 与 深市主板个股 撞号
    (("39",), ("sz",)),                # 399xxx 深证指数
    (("92", "43", "83", "87", "88"), ("bj", "sz", "sh")),
    (("00", "30", "12", "15", "16", "18"), ("sz", "sh")),
]


def _prefixes_for(code6: str) -> tuple:
    """给 6 位数字代码排出要探测的市场前缀顺序（长前缀优先匹配）。"""
    for heads, order in sorted(_PREFIX_PRIOR,
                               key=lambda x: -max(len(h) for h in x[0])):
        if code6.startswith(heads):
            return order
    return ("sz", "sh", "bj")


def resolve_symbol(raw: str) -> dict:
    """把用户输入解析为统一的标的描述（纯语法解析，不联网）。

    返回 dict:
      market   : A_SHARE / BJ_SHARE / HK_SHARE / US_SHARE / FUTURES
      tx_code  : 腾讯行情代码 (商品市场为 None)
      sina_sym : 新浪符号 (美股为 ticker，商品为 GC 这类符号)
      display  : 展示用代码
      probe    : 6 位裸数字时待探测的备选前缀（供 resolve_candidates 消歧）

    注意：6 位裸数字存在沪深北撞号，本函数只给出「先验最可能」的一个，
    页面与自检都应走 resolve_candidates() 拿到按活跃度排序的结果。
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

    # 显式市场前缀：sh600519 / sz000831 / bj920002
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", s):
        pfx = s[:2].lower()
        return {"market": _TX_PREFIX_MARKET[pfx], "tx_code": f"{pfx}{s[2:]}",
                "sina_sym": None, "display": s}

    # A 股 / 北交所 / ETF / 指数：6 位裸数字 —— 沪深北都可能有同号标的
    if re.fullmatch(r"\d{6}", s):
        order = _prefixes_for(s)
        return {"market": _TX_PREFIX_MARKET[order[0]], "tx_code": f"{order[0]}{s}",
                "sina_sym": None, "display": s, "probe": order}

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

def _parse_tx_quote(parts: list, market: str, tx_code: str = "") -> dict:
    """腾讯行情 ~ 分隔字段解析。A股 88 段 / 北交所 87 段 / 港股 78 段 / 美股 71 段，
    前 6 段与 30~45 段语义一致，可统一取值。

    tx_code 是带市场前缀的请求代码（sh000831）。段位里的 parts[2] 只有裸代码
    （000831），判类型时区分不出沪深，所以类型判定必须靠 tx_code。
    """
    if len(parts) < 40:
        return {}
    pe_raw = (parts[39] or "").strip()
    year_high = (parts[47] or "").strip() if len(parts) > 47 else ""
    q = {
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
        "amount_unit": "万元" if market in ("A_SHARE", "BJ_SHARE") else "元",
        "pe": _f(parts[39]),
        "mv_yi": _f(parts[45]) if len(parts) > 45 else 0.0,
        "update_time": parts[30],
        "_pe_raw": pe_raw,
        "_year_high_raw": year_high,
    }
    # 当日是否真的在交易。沪市指数在未开盘时依然返回昨收价，只是没有成交也没有
    # 当日高低价 —— 撞号消歧就靠这个判据，不能用「报价是否为空」。
    q["alive"] = q["amount"] > 0 or (q["high"] > 0 and q["low"] > 0)
    q["kind"] = _guess_kind(pe_raw, year_high, tx_code)
    return q


def _guess_kind(pe_raw: str, year_high_raw: str, tx_code: str) -> str:
    """从行情段位判标的类型，不依赖外部字典。

    **只对沪深北代码有效**：判据里的「52 周高/低恒为 -1」是 A 股行情段位的特性，
    港股 / 美股 / 商品的同位段语义不同（实测 hkHSI、usIXIC 会被误判成基金），
    所以非 sh/sz/bj 前缀直接返回空串 —— 宁可不标类型，也不给错的类型。

    A 股实测口径（19 个样本全对）：
      · 指数的「52 周高/低」(p47/p48) 恒为 -1，个股/基金有实值
      · 基金 / ETF / LOF 的 PE(p39) 为空字符串，个股与指数都有值
      · 转债的 p47 在沪市为 -1 且 PE 为空；深市转债 p47 有实值，故用代码段兜底
    """
    if not re.fullmatch(r"(sh|sz|bj)\d{6}", (tx_code or "").lower()):
        return ""
    digits = tx_code[2:]
    if digits.startswith(("110", "111", "113", "118", "123", "127", "128")):
        return "转债"
    idx_like = year_high_raw in ("-1", "-1.00", "-1.000")
    has_pe = pe_raw not in ("", "0", "0.00", "0.000")
    if idx_like:
        return "指数" if has_pe else "债券 / 其他"
    return "个股" if has_pe else "基金 / ETF"


def _fetch_tx_batch(tx_codes: list) -> dict:
    """一次请求取多个腾讯代码。返回 {tx_code: quote}。

    腾讯 q= 支持逗号拼接多个代码，不存在的标的**直接不返回该行**（既不是空串
    也不报错），所以探三个前缀与探一个的网络开销基本相同。
    注意：代码大小写必须与请求一致，全小写 usaapl 会整体返回 v_pv_none_match。
    """
    codes = [c for c in dict.fromkeys(tx_codes) if c]
    if not codes:
        return {}
    try:
        txt = _http_text("https://qt.gtimg.cn/q=" + ",".join(codes), encoding="gbk")
    except Exception:
        return {}
    out = {}
    for m in re.finditer(r'v_([a-zA-Z0-9\.\_]+)="([^"]*)"', txt):
        code, body = m.group(1), m.group(2)
        if not body or code not in codes:
            continue
        market = _TX_PREFIX_MARKET.get(code[:2].lower(), "A_SHARE")
        q = _parse_tx_quote(body.split("~"), market, code)
        if q and q.get("price", 0) > 0:
            q["tx_code"] = code
            q["market"] = market
            out[code] = q
    return out


def _fetch_tx_quote(tx_code: str, market: str) -> dict:
    try:
        txt = _http_text(f"https://qt.gtimg.cn/q={tx_code}", encoding="gbk")
        m = re.search(r'="([^"]*)"', txt)
        if not m or not m.group(1):
            return {}
        q = _parse_tx_quote(m.group(1).split("~"), market, tx_code)
        if q:
            q["tx_code"] = tx_code
            q["market"] = market
        return q
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
    if q:
        return q

    # 只在完全取不到时按前缀先验兜底探一圈。撞号消歧不在这里做 ——
    # 那必须在 resolve_candidates() 里比较活跃度，否则「有报价但没成交」的
    # 沪市指数会让兜底永不触发（这正是 000831 显示成 500低贝 的原因）。
    probe = sym.get("probe")
    if probe and len(sym.get("tx_code", "")) == 8:
        code6 = sym["tx_code"][2:]
        got = _fetch_tx_batch([f"{p}{code6}" for p in probe])
        best = _rank_candidates(list(got.values()))
        if best:
            sym["tx_code"] = best[0]["tx_code"]
            sym["market"] = best[0]["market"]
            return best[0]
    return q


# ==================== 2.5 撞号消歧 ====================

# 沪市 000xxx 段**全部**是指数，但会被用户主动按裸代码查询的只有这一小撮宽基。
# 其余（500低贝、上证周期、全R价值、180治理、上证商品…）是几乎无人主动查的衍生
# 指数 —— 那些代码上，用户输入裸 6 位数字的意图必然是同号的深市个股。
#
# 为什么需要这层先验：实测 31 个 000xxx 样本里 21 个沪深两市都在交易，仅靠活跃度
# 判据只能救 000831（沪市 500低贝 恰好无成交）这一类，救不了 000001 / 000063
# 这种双活跃的情况。改动此表请同步 tools_probe_quote_api.py 的撞号断言。
#
# 刻意不收 000002：沪市「Ａ股指数」远不如深市「万科Ａ」常被查询。
_MAJOR_SH_INDEX = {
    "000001",   # 上证指数
    "000010",   # 上证180
    "000016",   # 上证50
    "000300",   # 沪深300
    "000688",   # 科创50
    "000852",   # 中证1000
    "000903",   # 中证100
    "000905",   # 中证500
    "000906",   # 中证800
    "000922",   # 中证红利
}


def _prior_rank(q: dict) -> int:
    """相同代码、同样活跃时的取舍先验。数字越小越优先。"""
    code = q.get("tx_code", "")
    if code.startswith("sh") and q.get("kind") == "指数":
        return 0 if code[2:] in _MAJOR_SH_INDEX else 2
    return 1                      # 个股 / 基金 / 北交所 / 深市指数


def _rank_candidates(cands: list) -> list:
    """撞号排序：当日是否在交易 → 主流宽基指数先验 → 成交额。

    第一优先级必须是活跃度：沪市指数在未开盘时依然返回昨收价（amount=0、
    high/low=0），「取到报价」不等于「取对标的」。
    """
    return sorted(cands, key=lambda q: (0 if q.get("alive") else 1,
                                        _prior_rank(q),
                                        -q.get("amount", 0.0)))


@st.cache_data(ttl=8, show_spinner=False)
def resolve_candidates(raw: str) -> list:
    """把用户输入解析成**按活跃度排序**的候选标的列表。

    这是页面应当调用的入口。返回 [{sym, quote}, ...]，第一个即推荐结果；
    长度 > 1 说明确实存在撞号（如 000001 = 上证指数 + 平安银行），此时页面
    必须把选择权交还用户，不能静默替他决定。

    非 6 位裸数字的输入（sh000831 / 00700 / NVDA / GC）只会有 0 或 1 个候选。
    """
    sym = resolve_symbol(raw)
    if not sym:
        return []

    probe = sym.get("probe")
    if not probe:
        q = get_quote(sym)
        if not q or q.get("price", 0) <= 0:
            return []
        return [{"sym": sym, "quote": q}]

    code6 = sym["display"]
    got = _fetch_tx_batch([f"{p}{code6}" for p in probe])
    out = []
    for q in _rank_candidates(list(got.values())):
        out.append({
            "sym": {"market": q["market"], "tx_code": q["tx_code"],
                    "sina_sym": None, "display": code6, "probe": probe},
            "quote": q,
        })
    return out


def candidate_label(item: dict) -> str:
    """候选项的一行说明：市场 + 类型 + 名称 + 是否在交易。"""
    q, sym = item["quote"], item["sym"]
    mkt = {"sh": "沪市", "sz": "深市", "bj": "北交所"}.get(sym["tx_code"][:2], "")
    state = "今日有成交" if q.get("alive") else "今日无成交"
    return f"{mkt}{q.get('kind', '')} · {q.get('name', '')} · {state}"


# ==================== 2.6 名称搜索 ====================

_SEARCH_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                   "Referer": "https://stockapp.finance.qq.com/"}

# smartbox 返回的类型码 → 站内可查询的输入。jj(场外基金)/QZ(窝轮) 本站无行情通道，
# 直接过滤掉，避免用户点了个查不出东西的结果。
_SEARCH_TYPE_LABEL = {
    "GP-A": "A股", "GP-A-KCB": "科创板", "GP-B": "B股", "ZS": "指数",
    "ETF": "ETF", "QDII-ETF": "QDII-ETF", "LOF": "LOF", "QDII-LOF": "QDII-LOF",
    "GP": "股票",
}
_SEARCH_OK_PREFIX = {"sh", "sz", "bj", "hk", "us"}
_MARKET_HINT = {"sh": "沪市", "sz": "深市", "bj": "北交所", "hk": "港股", "us": "美股"}

# 东财 suggest —— 只用来补 smartbox 的盲区，见 _search_eastmoney。
_SEARCH_EM_URL = ("https://searchapi.eastmoney.com/api/suggest/get?input={kw}"
                  "&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count={n}")
# 北交所与北证指数的代码段。**注意 43x/83x/87x/88x 同时也是新三板段**，
# 所以段位必须叠加东财的 SecurityTypeName（京A / 指数）才能用，见 _em_search_hit。
_EM_BJ_SEGMENTS = ("92", "43", "83", "87", "88", "899")


class _SearchEmpty(Exception):
    """搜索无结果。

    **刻意用异常而不是返回空列表**：实测 st.cache_data 会把空列表按 ttl 整整缓存
    10 分钟（连打 3 次只实际联网 1 次），但对抛异常的调用完全不缓存（3 次调用
    联网 3 次）。搜索接口有过瞬时空返回，若首次搜索正好撞上而返回 []，用户之后
    十分钟内怎么搜都是空 —— 「一次抖动锁死十分钟」比多打一次请求糟得多。
    """


def _decode_search_name(name: str) -> str:
    """smartbox 的名称字段是 \\uXXXX 转义，需二次解码。"""
    if "\\u" not in name:
        return name
    try:
        return name.encode("utf-8").decode("unicode_escape")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name


@st.cache_data(ttl=600, show_spinner=False)
def _search_kernel(keyword: str, limit: int) -> list:
    """搜索内核。无结果时**抛 _SearchEmpty**（原因见该异常的说明），不返回空列表。

    两个源，职责分明：
      1. smartbox.gtimg.cn —— 主源，覆盖 A股 / 科创 / B股 / 港美股 / ETF / LOF / 指数，
         支持中文名与拼音缩写（maotai → 贵州茅台，ndsd → 宁德时代）。
      2. 东财 suggest —— **只补主源的盲区**：实测 smartbox 对北交所（锦波生物 /
         贝特瑞 / 武汉蓝电 / 920982 / 899050）与可转债（立讯转债 / 南银转债 /
         128136 / 113050）一律返回 0 条，中文名、拼音、纯代码三种写法全都搜不到。

    补完再用腾讯批量报价统一校验一遍：**搜索结果必须是站内真能查出行情的标的**，
    否则等于给用户一个点了报错的按钮。这一步顺手挡掉东财会混进来的新三板
    （831071 北塔软件）、企业债（751240）、英股（BTRW）—— 它们在腾讯没有报价。
    """
    kw = (keyword or "").strip()
    if not kw:
        raise _SearchEmpty("empty keyword")

    hits = _search_smartbox(kw, limit)
    if len(hits) < limit:
        seen = {h["query"] for h in hits}
        for h in _search_eastmoney(kw, limit - len(hits)):
            if h["query"] not in seen:
                seen.add(h["query"])
                hits.append(h)

    hits = _drop_unquotable(hits)
    if not hits:
        raise _SearchEmpty(kw)
    return hits[:limit]


def search_symbols(keyword: str, limit: int = 10) -> list:
    """按名称 / 拼音 / 代码搜索标的。返回 [{query, code, name, market_hint, type_label}]。

    这是**纯增强功能**：所有失败都收敛成空列表，页面必须保证搜索挂掉时纯代码
    输入照常可用。缓存策略与空结果的处理见 `_search_kernel` 与 `_SearchEmpty`。
    """
    try:
        return _search_kernel(keyword, limit)
    except _SearchEmpty:
        return []
    except Exception:
        return []


def _search_smartbox(kw: str, limit: int) -> list:
    """主源。格式 `前缀~代码~名称~拼音~类型码`，^ 分隔，GBK 编码。

    坑：**响应是 GBK，但查询词必须按 UTF-8 百分号编码**，否则中文一律搜不到而
    英文正常 —— 这种「中文挂英文通」先查参数编码，别先怀疑限流。
    """
    try:
        url = "https://smartbox.gtimg.cn/s3/?t=all&q=" + urllib.parse.quote(kw)
        txt = _http_text(url, headers=_SEARCH_HEADERS, encoding="gbk", timeout=6)
    except Exception:
        return []
    m = re.search(r'v_hint="([^"]*)"', txt)
    if not m or not m.group(1):
        return []

    out, seen = [], set()
    for item in m.group(1).split("^"):
        f = item.split("~")
        if len(f) < 3:
            continue
        pfx, code, tp = f[0].lower(), f[1], (f[4] if len(f) > 4 else "")
        if pfx not in _SEARCH_OK_PREFIX or tp not in _SEARCH_TYPE_LABEL:
            continue
        # 站内查询用的输入：A股带显式前缀避免再次撞号；港美股用代码本身
        if pfx in ("sh", "sz", "bj"):
            query = f"{pfx}{code}"
        elif pfx == "hk":
            query = code if not code.isdigit() else code.zfill(5)
        else:
            query = code.split(".")[0].upper()
        if query in seen:
            continue
        seen.add(query)
        out.append({
            "query": query,
            "code": code,
            "name": _decode_search_name(f[2]),
            "market_hint": _MARKET_HINT[pfx],
            "type_label": _SEARCH_TYPE_LABEL[tp],
            "source": "smartbox",
        })
        if len(out) >= limit:
            break
    return out


def _em_search_hit(row: dict) -> dict:
    """东财 suggest 的一行 → 站内候选。不属于要补的盲区则返回 {}。

    只认北交所与可转债两类。其余（沪深A股、港美股、ETF、指数）主源已覆盖，
    放进来只会引入东财特有的噪音：场外基金 OTCFUND、英股 LSE、粉单 OTCBB、
    板块 BK、新三板三板 —— 搜「btr」东财一次就能返 4 条这类干扰项。

    字段坑：北交所的 `MktNum` 也是 0，与深A 完全一样，**只看 MktNum 会把北交所
    当深市**，必须靠 `SecurityTypeName`（京A / 指数）+ 代码段一起判。
    """
    code = str(row.get("QuoteID") or "").split(".")[-1]
    if not re.fullmatch(r"\d{6}", code):
        return {}
    name = (row.get("Name") or "").strip()
    if not name:
        return {}
    classify = row.get("Classify") or ""
    type_name = (row.get("SecurityTypeName") or "").strip()
    mkt = str(row.get("MktNum") or "")

    # 可转债：Classify=Bond 且落在转债代码段（企业债 751xxx 同为 Bond，段位挡掉）。
    # 前缀直接用 MktNum（1=沪 / 0=深），比按段位推更稳 —— 段位只负责「是不是转债」。
    if classify == "Bond" and code.startswith(("110", "111", "113", "118",
                                               "123", "127", "128")):
        pfx = "sh" if mkt == "1" else "sz"
        return {"query": f"{pfx}{code}", "code": code, "name": name,
                "market_hint": "沪市" if pfx == "sh" else "深市",
                "type_label": "转债", "source": "eastmoney"}

    # 北交所个股与北证指数：新三板同为 NEEQ 但 SecurityTypeName 是「三板」，
    # 且不在 92/899 段 —— 两道判据叠加才不会把三板放进来。
    is_bj_stock = type_name == "京A" and classify == "NEEQ"
    is_bj_index = type_name == "指数" and code.startswith("899") and mkt == "0"
    if (is_bj_stock or is_bj_index) and code.startswith(_EM_BJ_SEGMENTS):
        return {"query": f"bj{code}", "code": code, "name": name,
                "market_hint": "北交所",
                "type_label": "指数" if is_bj_index else "个股",
                "source": "eastmoney"}
    return {}


def _search_eastmoney(kw: str, limit: int) -> list:
    """兜底源。只取北交所 / 可转债，见 _em_search_hit 的过滤理由。"""
    if limit <= 0:
        return []
    try:
        url = _SEARCH_EM_URL.format(kw=urllib.parse.quote(kw), n=10)
        data = _http_json(url, headers=_UA, timeout=6)
    except Exception:
        return []
    rows = (data.get("QuotationCodeTable") or {}).get("Data") or []
    out, seen = [], set()
    for row in rows:
        hit = _em_search_hit(row)
        if not hit or hit["query"] in seen:
            continue
        seen.add(hit["query"])
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def _drop_unquotable(hits: list) -> list:
    """丢掉腾讯取不到报价的候选 —— 搜索结果必须点了真能查出东西。

    一次请求批量校验（实测 30 个代码一发没问题，不存在的标的直接不返回该行）。
    **报价接口整体失败时原样放行**：那是网络问题，不该让校验步骤把搜索废掉；
    宁可放一个可能查不出的按钮，也不能让所有人都搜不到东西。
    """
    if not hits:
        return []
    probe, order = [], []
    for h in hits:
        sym = resolve_symbol(h["query"])
        code = sym.get("tx_code") if sym else None
        order.append(code)
        if code:
            probe.append(code)
    if not probe:
        return hits
    try:
        got = _fetch_tx_batch(probe)
    except Exception:
        return hits
    if not got:                       # 接口整体挂了，降级放行
        return hits
    return [h for h, code in zip(hits, order) if code and code in got]



# ==================== 3. 分时走势 ====================

@st.cache_data(ttl=20, show_spinner=False)
def get_minute_line(sym: dict) -> pd.DataFrame:
    """当日分时。A股/港股走腾讯，美股走新浪(腾讯只返回收盘一个点)，商品走新浪全球期货。"""
    market = sym.get("market")
    try:
        if market in ("A_SHARE", "BJ_SHARE", "HK_SHARE"):
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

# 日/周/月 K 的端点选择。北交所必须走 newfqkline —— 老 fqkline 对 bj 代码只返回
# 当天 1 根，既不报错也不为空。单点定义，勿在别处再写一份。
_TX_KLINE_EP = {"A_SHARE": "fqkline", "BJ_SHARE": "newfqkline",
                "HK_SHARE": "hkfqkline", "US_SHARE": "usfqkline"}

# 前复权日K 单次请求的根数上限。**这是个静默悬崖**：limit<=800 如实返回，
# limit>=801 会退回到 640 根（实测 sz300750/sh601869/sh600519/bj920982 一致，
# 精确边界 800→800 根、801→640 根），limit>=3000 直接返回 0 根 —— 既不报错也不
# 提示。传 1000 想要更多历史，实际拿到的比传 800 还少。
# 只对 qfq 生效（不复权、指数不受此限），所以「换个标的试试」容易得出错误结论。
_TX_QFQ_MAX_LIMIT = 800

# 区间取数最多分几段往前补（每段 <=800 根，12 段 ≈ 39 年，覆盖任何 A 股完整历史）。
# 设上限只为防接口异常时死循环，正常情况会因「拉不到更早数据」提前退出。
_RANGE_MAX_SEGMENTS = 12


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
        ep = _TX_KLINE_EP[market]
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/{ep}/get"
               f"?param={code},{p},,,{min(limit, _TX_QFQ_MAX_LIMIT)},qfq")
    try:
        d = _http_json(url, timeout=12)
        node = (d.get("data") or {}).get(code) or {}
        arr = node.get(p) or node.get(f"qfq{p}") or []
        if not arr:
            # 有些标的返回 key 不带 qfq 前缀，兜底取最长的那个 list
            best = []
            for v in node.values():
                if isinstance(v, list) and len(v) > len(best):
                    best = v
            arr = best
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


@st.cache_data(ttl=1800, show_spinner=False)
def get_daily_kline_range(tx_code: str, market: str, start: str, end: str) -> pd.DataFrame:
    """取指定日期区间的前复权日 K。**给「按区间画图」的页面用**（黄金分割等）。

    与 get_kline 的区别：get_kline 只要「最近 N 根」，这里要「某年某月到某年某月」。
    腾讯这两件事的参数语义完全不同，实测三条硬约束（每条都反复打了 3 轮排除抖动）：

    1. `limit` 是**总根数上限、且有个静默悬崖**：limit<=800 如实返回，limit>=801
       退回 640 根（比传 800 还少），limit>=3000 直接返回 0 根。fqkline 与
       newfqkline 都是这样。所以覆盖长区间只能分段拉再合并，把 limit 调大反而更少
       —— 这正是老实现「传 1000 却只拿到 641 根」的成因（2021 年至今应有 1374 根）。
    2. `start` / `end` **都是闭合的**（`end=2026-08-28` 末根就是 08-28）。**但**
       `end` 落在**当天**时，同一天里**有的 limit 给当天、有的不给**，而且是
       **稳定可复现**的（同参数连打 4 次结果完全一致，不是网络抖动）：
       2026-09-02 实测 `start=2026-06-01` 总共才 66 根、远没顶满，
       `limit=200/800` 照样丢当天，`limit=100/300/400/700` 却都给 ——
       所以**根本不是「顶满 limit 就丢一根」**（我第一版就是这么误判的），
       更像服务端按 limit 分了若干缓存分片、部分分片当天还没刷新。
       跨 6 标的 × 3 个 start × 5 个 limit 实测命中当天的比例：
       `limit=300` 是 18/18、`100` 是 17/18、`800` 是 13/18、`640` 只有 10/18；
       且**任何分片都只会少给最新一根、从不多给** —— 取各分片最大日期就是真实
       最新交易日（6 个标的复核全部 == 当天）。
       所以这里不猜规则，用**实得数据**判断：拉完一段后若 `max(实得) < end`，
       就用 `[max(实得), end]` 这个窄区间补一次尾（窄区间走另一个分片，实测
       `limit` 取 100/300/800 都能补到当天）。
       `end` 写成过去某天（如 08-28）时不存在这个现象 —— 因此**验证补尾逻辑
       必须用 `end=当天`**，用固定的过去日期根本触发不到。
    3. 北交所必须走 newfqkline（老 fqkline 对 bj 代码静默只返回 1 根），
       **newfqkline 同样认 start/end**，只是同样有 800 的悬崖 ——
       所以两个端点走完全一样的分段逻辑，不必为北交所写特例。

    分段拉取的复权基准实测不漂移（重叠段收盘价完全一致），因此合并是安全的。
    **必须循环补到 start**：只补一段的话，请求 2015 年起的茅台会静默从 2020-02-05
    才开始（800+800 去重恰好 1599 根），外观完全正常 —— 这正是本函数要消灭的那类
    静默残缺，别在自己身上再犯一次。实测请求次数：5 年 3 次、25 年 9 次。
    """
    if not tx_code or not start or not end:
        return pd.DataFrame()
    ep = _TX_KLINE_EP.get(market, "fqkline")
    merged = {}

    def _pull(seg_start: str, seg_end: str) -> int:
        u = (f"https://web.ifzq.gtimg.cn/appstock/app/{ep}/get"
             f"?param={tx_code},day,{seg_start},{seg_end},{_TX_QFQ_MAX_LIMIT},qfq")
        try:
            d = _http_json(u, timeout=12)
        except Exception:
            return 0
        node = (d.get("data") or {}).get(tx_code) or {}
        arr = node.get("qfqday") or node.get("day") or []
        if not arr:
            # 有些标的返回 key 不带 qfq 前缀，兜底取最长的那个 list
            best = []
            for v in node.values():
                if isinstance(v, list) and len(v) > len(best):
                    best = v
            arr = best
        before = len(merged)
        for it in arr:
            if len(it) >= 6:
                merged[str(it[0])] = it
        return len(merged) - before

    _pull(start, end)
    # 补尾：`end` 落在当天时，某些 limit 分片当天还没刷新，末根会停在前一个交易日
    # （见 docstring 第 2 条，与是否顶满 limit 无关）。窄区间走的是另一个分片，
    # 实测能补到当天。
    if merged and max(merged) < end:
        _pull(max(merged), end)
    # 往前回补，直到覆盖 start 或再拉不出更早的数据（已到上市日）
    for _ in range(_RANGE_MAX_SEGMENTS):
        if not merged or min(merged) <= start:
            break
        if _pull(start, min(merged)) == 0:
            break

    rows = [merged[k] for k in sorted(merged) if start <= str(k) <= end]
    df = _tx_kline_records(rows)
    return df.rename(columns={"datetime": "date"}) if not df.empty else df


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
    # 北交所与沪深同步，9:30-11:30 / 13:00-15:00
    "BJ_SHARE": ([("09:30", "11:30"), ("13:00", "15:00")],
                 ["09:30", "10:30", "11:30", "14:00", "15:00"],
                 ["09:30", "10:30", "11:30/13:00", "14:00", "15:00"]),
    # 港股连续竞价 16:00 收盘，之后 16:01-16:10 是收市竞价(CAS)只有一个成交点，
    # 若把轴拉到 16:10 会让右侧出现一段空白 + 孤点，故轴止于 16:00，CAS 价并入收盘分钟。
    "HK_SHARE": ([("09:30", "12:00"), ("13:00", "16:00")],
                 ["09:30", "10:30", "12:00", "14:30", "16:00"],
                 ["09:30", "10:30", "12:00/13:00", "14:30", "16:00"]),
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


def _align_to_timeline(df: pd.DataFrame, timeline: list) -> pd.DataFrame:
    """把分时点吸附到交易时段轴上。

    行情源常返回不在标准时段内的时间戳：A股收盘后集合竞价/延时快照会出现
    15:06~15:30，港股恒指有 18:31 的期指延伸报价，港股收市竞价落在 16:00 之后。
    这些点若原样 merge 会全部变成 NaN，画出来就是「曲线只画到一半」的错位。
    统一吸附到不晚于它的最后一个轴刻度（早于开盘的吸附到第一个刻度），同一
    刻度保留最后一条。
    """
    if df.empty or not timeline:
        return df
    slot_min = [int(t[:2]) * 60 + int(t[3:5]) for t in timeline]
    base = slot_min[0]
    adj = [m + 1440 if m < base else m for m in slot_min]  # 跨零点(商品)展平
    known = set(timeline)

    def snap(t: str) -> str:
        if t in known:
            return t
        try:
            m = int(t[:2]) * 60 + int(t[3:5])
        except (ValueError, IndexError):
            return timeline[-1]
        m = m + 1440 if m < base else m
        pos = [i for i, a in enumerate(adj) if a <= m]
        return timeline[pos[-1]] if pos else timeline[0]

    out = df.copy()
    out["time"] = out["time"].map(snap)
    return out.drop_duplicates(subset="time", keep="last")


# ==================== 6. 页面渲染 ====================

PRESETS = [
    ("贵州茅台", "600519"), ("上证指数", "sh000001"), ("中国稀土", "sz000831"),
    ("纳指ETF", "513100"), ("腾讯控股", "00700"), ("恒生指数", "HSI"),
    ("英伟达", "NVDA"), ("纽约黄金", "GC"),
]


def _fmt(v: float, market: str) -> str:
    return f"{v:.{_PRICE_DECIMALS.get(market, 2)}f}"


def _render_minute_chart(sym: dict, quote: dict, df: pd.DataFrame):
    market = sym["market"]
    name = quote.get("name", sym["display"])
    timeline, tick_vals, tick_texts = _timeline(market)
    df = _align_to_timeline(df, timeline)

    price = pd.to_numeric(df["price"], errors="coerce")
    vol = pd.to_numeric(df.get("vol"), errors="coerce").fillna(0.0)
    df = df.assign(price=price, vol=vol).dropna(subset=["price"])
    if df.empty:
        st.info("暂无分时数据")
        return
    price, vol = df["price"], df["vol"]

    last_close = quote.get("last_close") or float(price.iloc[0])
    dev = max(abs(float(price.max()) - last_close), abs(float(price.min()) - last_close))
    if dev <= 0:
        dev = max(abs(last_close) * 0.01, 0.01)
    y_min, y_max = last_close - dev * 1.15, last_close + dev * 1.15

    # cum_v 为 0 的分钟（开盘瞬间未成交）不能用 replace(0, pd.NA)：pandas 3.x 下
    # 结果 dtype 会退化成 object，随后 .astype(float) 直接 TypeError 崩页。
    # 用 where 保持 float64 + NaN。
    cum_v = vol.cumsum()
    if float(cum_v.iloc[-1]) > 0:
        df["avg_price"] = ((price * vol).cumsum() / cum_v.where(cum_v > 0)).astype("float64")
    else:
        df["avg_price"] = price.expanding().mean().astype("float64")

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


def _render_kline_chart(sym: dict, quote: dict, df: pd.DataFrame, period: str,
                        modes: list = None, show_stroke: bool = True,
                        show_ma: bool = True):
    """
    K 线主图。modes 为空 → 只画蜡烛 + MA；否则按选中的分析体系叠加图层。
    返回 analyze() 的结果，供调用方渲染文字结论与 MACD 副图（避免重复计算）。
    """
    name = quote.get("name", sym["display"])
    modes = modes or []

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["datetime"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=period, increasing_line_color="#ff4d4d", decreasing_line_color="#2ecc71"))

    # 叠加帝纳波利 DMA 时不再画普通 MA，避免图上七八条线糊成一片
    if show_ma and tov._MODE_DINAPOLI not in modes:
        for w, color in ((5, "#f39c12"), (20, "#3498db")):
            if len(df) >= w:
                fig.add_trace(go.Scatter(x=df["datetime"], y=df["close"].rolling(w).mean(),
                                         name=f"MA{w}", line=dict(color=color, width=1.4)))

    res = ta.analyze(df) if modes else {"ok": False, "reason": ""}
    if modes and res.get("ok"):
        if tov._MODE_CHAN in modes:
            tov.draw_chan(fig, df, res, show_stroke=show_stroke)
        if tov._MODE_DINAPOLI in modes:
            tov.draw_dinapoli(fig, df, res)

    title = f"{name} · {period}（{len(df)} 根）"
    if modes and res.get("ok"):
        title += " · " + " + ".join(modes)
    fig.update_layout(
        title=title,
        xaxis=dict(rangeslider=dict(visible=False), tickangle=-45, type="category",
                   nticks=14, gridcolor="rgba(128,128,128,0.2)"),
        yaxis=dict(title="价格", gridcolor="rgba(128,128,128,0.2)"),
        hovermode="x unified", height=600 if modes else 520,
        legend=dict(orientation="h", y=1.06, x=0),
    )
    st.plotly_chart(fig, use_container_width=True)
    return res


def _render_search_hits(hits: list, key_tag: str, state_key: str = "active_symbol"):
    """把搜索结果渲染成一排可点按钮。点击即切换标的。

    state_key 必须是**当前页面**真正读取的那个 session 键 —— 黄金分割页与行情页
    各有自己的输入框，写错键会让按钮看着能点却毫无反应。
    """
    cols = st.columns(min(len(hits), 4))
    for i, h in enumerate(hits):
        with cols[i % len(cols)]:
            if st.button(f"{h['name']}\n`{h['query']}`",
                         key=f"{key_tag}_{h['market_hint']}_{h['query']}",
                         use_container_width=True,
                         help=f"{h['market_hint']} · {h['type_label']} · {h['code']}"):
                st.session_state[state_key] = h["query"]
                st.rerun()


def _render_search_box(key_tag: str = "sr", state_key: str = "active_symbol",
                       widget_key: str = "lq_search_kw"):
    """名称 / 拼音搜索。纯增强：接口挂掉时不影响下方的代码输入。"""
    kw = st.text_input(
        "🔎 按名称搜索（可选）",
        key=widget_key,
        placeholder="中国稀土 / maotai / 宁德时代 / 锦波生物 / 立讯转债 / 英伟达",
        help="支持中文名、拼音缩写、代码。搜到结果后点一下即可切换标的，"
             "省得记 6 位代码到底该配沪市还是深市。",
    )
    if not kw or not kw.strip():
        return
    hits = search_symbols(kw.strip())
    if not hits:
        st.caption("🔎 没搜到可查询的标的（场外基金、窝轮、新三板本站没有行情通道，已过滤）。"
                   "也可能是搜索接口临时无响应 —— 直接在下面填代码同样可用。")
        return
    st.caption(f"🔎 找到 {len(hits)} 个，点一下切换：")
    _render_search_hits(hits, key_tag, state_key)


def _fallback_to_search(raw: str, key_tag: str = "fb",
                        state_key: str = "active_symbol") -> bool:
    """代码框里填的其实是名字时，直接在原地把搜索结果递给用户。

    实测这是最容易踩的坑：搜索框与代码框上下紧邻，把「宁德时代」打进代码框只会
    得到「无法识别代码」，而「ndsd」更糟 —— 它符合美股 ticker 的语法，会被解析
    成 usNDSD 然后报「未取到行情」，两种情况下用户都看不出该改用上面那个框。

    返回是否给出了候选（给出了就不必再让用户自己找搜索框）。
    """
    hits = search_symbols(raw.strip())
    if not hits:
        return False
    st.info(f"「{raw}」看着像名称而不是代码。下面是搜到的标的，点一下直接切过去：")
    _render_search_hits(hits, key_tag, state_key)
    return True



def _pick_candidate(raw: str, cands: list, key_prefix: str = "lq") -> dict:
    """撞号时把选择权交还用户；只有一个候选则直接返回。

    绝不静默替用户决定 —— 000831 沪市是指数「500低贝」、深市是个股「中国稀土」，
    两者都是真实标的，系统只能给出排序建议（谁在交易），不能假装另一个不存在。

    key_prefix 用于多页复用时隔离 widget key（同一进程里两页都渲染 000831 的
    radio 会撞 key 直接抛 StreamlitDuplicateElementKey）。
    """
    if len(cands) == 1:
        return cands[0]

    key = f"{key_prefix}_pick_{raw}"
    labels = [candidate_label(c) for c in cands]
    st.warning(f"代码 `{raw}` 在多个市场都有标的。默认选中当日有成交的那个，"
               f"如需另一个请在下面切换 —— 或直接输入带市场前缀的写法"
               f"（如 `{cands[0]['sym']['tx_code']}`）避免每次都要选。")
    choice = st.radio("🧭 你要看哪一个？", labels, index=0, key=key, horizontal=False)
    return cands[labels.index(choice)]


def render_live_quote_page():
    st.header("⚡ 全市场实时行情与自动技术分析")
    st.caption("A股 / ETF / 指数 · 北交所 · 港股 · 美股 · 国际大宗商品 —— 周期选项按各市场实际可取到的数据动态生成；"
               "K 线周期支持自动画缠论结构（笔 / 线段 / 中枢）与帝纳波利位置（DMA / F3·F5 回撤 / COP·OP·XOP 目标位）")

    if "active_symbol" not in st.session_state:
        st.session_state.active_symbol = "600519"

    st.markdown("**🔥 快速直达**")
    cols = st.columns(4)
    for i, (label, code) in enumerate(PRESETS):
        with cols[i % 4]:
            if st.button(f"{label} `{code}`", key=f"pre_{code}", use_container_width=True):
                st.session_state.active_symbol = code
                st.rerun()

    _render_search_box()

    c_in, c_btn = st.columns([4, 1])
    with c_in:
        raw = st.text_input(
            "🔍 标的代码",
            value=st.session_state.active_symbol,
            help="A股/ETF/指数: 600519、000001、513100 ｜ 显式指定市场: sh000831 沪市指数、"
                 "sz000831 深市个股、bj920002 北交所 ｜ 港股: 00700、HSI ｜ "
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
        st.info("请输入标的代码，或用上面的名称搜索。")
        return

    if not resolve_symbol(raw):
        st.error(f"无法识别代码 `{raw}`。A股/北交所填 6 位数字（或 sh/sz/bj 前缀），"
                 f"港股 5 位数字，美股填字母代码，商品填 GC / CL 这类符号。")
        _fallback_to_search(raw)
        return

    cands = resolve_candidates(raw)
    if not cands:
        st.error(f"未取到 `{raw}` 的行情。沪深北三个市场都没有这个代码的有效报价，"
                 f"请确认代码是否正确。")
        _fallback_to_search(raw)
        return

    picked = _pick_candidate(raw, cands)
    sym, quote = picked["sym"], picked["quote"]

    market = sym["market"]
    pct = quote.get("pct_chg", 0.0)

    k = st.columns(5)
    kind = quote.get("kind", "")
    k[0].metric("标的", quote.get("name", sym["display"]),
                delta=f"{MARKET_LABEL[market]}{' · ' + kind if kind else ''}",
                delta_color="off")
    k[1].metric("最新价", _fmt(quote.get("price", 0), market), delta=f"{pct:+.2f}%")
    k[2].metric("最高 / 最低",
                f"{_fmt(quote.get('high', 0), market)} / {_fmt(quote.get('low', 0), market)}")
    amt = quote.get("amount", 0.0)
    if market in ("A_SHARE", "BJ_SHARE") and amt:
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
        st.caption("ℹ️ 自动技术分析作用于 K 线周期，分时图不做结构识别 —— "
                   "缠论的笔与帝纳波利的摆动点都需要完整 K 线的高低点。")
        return

    df = get_kline(sym, period)
    if df.empty:
        st.info(f"暂未取到 {period} 数据，数据源可能临时无响应，稍后重试或换个周期。")
        return
    if len(df) < 2:
        st.warning(f"{period} 只取到 {len(df)} 根 K 线，无法做结构分析。"
                   f"这类情况多见于北交所老三板代码（430xxx / 83xxxx）—— 公开接口没有它们的历史行情。")
        return

    st.markdown("**🧠 自动技术分析**")
    a1, a2, a3 = st.columns([3, 1.2, 1.2])
    with a1:
        modes = st.multiselect(
            "分析体系（可多选，留空则只看普通 K 线）",
            tov.ANALYSIS_MODES, default=[tov._MODE_CHAN],
            key=f"ta_modes_{market}",
            help="缠论：K线包含处理 → 分型 → 笔 → 线段 → 中枢；"
                 "帝纳波利：3x3/7x5/25x5 位移均线 + F3/F5 斐波那契回撤位 + COP/OP/XOP 目标位 + 汇聚区")
    with a2:
        show_stroke = st.checkbox("显示笔", value=True, key=f"ta_stroke_{market}",
                                  help="关掉后只保留高一级的线段，图面更干净")
    with a3:
        show_macd = st.checkbox("MACD 副图", value=bool(modes), key=f"ta_macd_{market}",
                                help="8/17/9 快参数 + 自动标注顶/底背离")

    res = _render_kline_chart(sym, quote, df, period,
                             modes=modes, show_stroke=show_stroke)

    if not modes:
        st.caption("ℹ️ 勾选上方的分析体系即可在图上自动画出缠论结构或帝纳波利位置。")
        return

    if not res.get("ok"):
        st.warning(f"无法完成结构分析：{res.get('reason', '未知原因')}")
        return

    if show_macd:
        st.plotly_chart(tov.draw_macd(df, res), use_container_width=True)

    tov.render_conclusion(res, period, modes)

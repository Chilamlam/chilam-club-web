# -*- coding: utf-8 -*-
"""标的代码解析的**唯一真源**：纯代码 → 腾讯行情代码（含沪深北撞号消歧）。

为什么要单独一个模块：这条规则原先有四处实现（page_live_quote 的正确版 +
page_watchlist._tx_code + page_watchlist 雪球链接 + daily_digest._tx_code），
后三处都是朴素的「6/5/9 开头算沪市，否则算深市」，2026-09-03 实测后果：

    输入 000905（用户想看中证500）→ sz000905 → 腾讯返回「厦门港务」8.90 元
    输入 000300（沪深300）        → sz000300 → 腾讯**根本没有这个标的**，取不到
    输入 000016（上证50）        → sz000016 → 返回「*ST康佳A」2.46 元

图能画出来、表格能显示、涨跌幅有数字，只是全是另一只标的的 —— **静默取错比
取不到更贵**。所以本模块的核心不是「拼前缀」，而是「联网探测后按活跃度取舍，
并把是否存在歧义如实告诉调用方」。

本模块**刻意只依赖标准库**（无 streamlit / pandas），因此展示层与跑批层可以共用：
    page_live_quote.py  展示层（另有 kind 判定与多市场，从这里取前缀先验）
    page_watchlist.py   展示层
    daily_digest.py     跑批层（不能 import streamlit）

消歧判据的依据（2026-09-03 实测 qt.gtimg.cn）：
  · 不存在的标的**直接不返回该行**，既不是空串也不报错 → 探三个前缀与探一个开销相同
  · 沪市指数在未开盘时**依然返回昨收价**，只是 amount=0 且 high/low=0
    → 「取到报价」不等于「取对标的」，消歧第一判据必须是当日是否真的在交易
  · 段位语义（A股 88 段 / 北交所 87 段）：
      p[1]名称 p[2]裸代码 p[3]现价 p[4]昨收 p[30]时间 p[32]涨跌幅%
      p[33]最高 p[34]最低 p[37]成交额(万元) p[38]换手率% p[39]PE
      p[43]振幅%（= (高-低)/昨收*100，已用四只标的核对一致）
      p[44]流通市值(亿) p[45]总市值(亿)（工行 22162 vs 29296 可区分二者）
      p[47]/p[48] 52周高/低，指数恒为 -1
"""
from __future__ import annotations

import re
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_QUOTE_URL = "https://qt.gtimg.cn/q="
_BATCH = 50                      # 每批代码数，避免 URL 过长

TX_PREFIX_MARKET = {"sh": "A_SHARE", "sz": "A_SHARE", "bj": "BJ_SHARE",
                    "hk": "HK_SHARE", "us": "US_SHARE"}

# 日/周/月 K 端点。北交所必须走 newfqkline —— 老 fqkline 对 bj 代码只返回当天
# 1 根，既不报错也不为空（2026-09-03 复测 bj920982：fqkline 1 根 / newfqkline 260 根）。
KLINE_EP = {"A_SHARE": "fqkline", "BJ_SHARE": "newfqkline",
            "HK_SHARE": "hkfqkline", "US_SHARE": "usfqkline"}

# 6 位代码的前缀先验。同一串数字沪深北最多同时存在三个标的，这里只决定
# 「都活跃时优先谁」，真正的取舍靠 rank_key 的活跃度判据。
PREFIX_PRIOR = [
    (("60", "68", "90", "58", "56", "51", "50", "11", "13", "20", "73"), ("sh", "sz")),
    (("999",), ("sh",)),
    (("000",), ("sh", "sz")),          # 000xxx: 上证指数系列 与 深市主板个股 撞号
    (("39",), ("sz",)),                # 399xxx 深证指数
    (("92", "43", "83", "87", "88"), ("bj", "sz", "sh")),
    (("00", "30", "12", "15", "16", "18"), ("sz", "sh")),
]

# 撞号且双方都活跃时，优先认成沪市宽基指数的代码。
# 判据只能救「沪市那只恰好无成交」之外的情况，救不了 000001（上证指数与平安银行
# 都活跃）—— 那种情况必须靠 ambiguous 标记把选择权交还用户。
# 改动此表请同步 tools_probe_quote_api.py 与 tools_probe_watchlist.py 的撞号断言。
MAJOR_SH_INDEX = {
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


def bare(code) -> str:
    """去掉 .SH/.SZ 后缀与市场前缀，取纯数字/纯 ticker。用于跨数据源对齐。"""
    s = str(code or "").strip().upper().split(".")[0]
    m = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", s)
    return m.group(2) if m else s


def prefixes_for(code6: str) -> tuple:
    """给 6 位数字代码排出要探测的市场前缀顺序（长前缀优先匹配）。"""
    for heads, order in sorted(PREFIX_PRIOR,
                               key=lambda x: -max(len(h) for h in x[0])):
        if code6.startswith(heads):
            return order
    return ("sz", "sh", "bj")


def market_of(tx_code: str) -> str:
    return TX_PREFIX_MARKET.get(str(tx_code or "")[:2].lower(), "A_SHARE")


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ================= 报价段位解析（纯函数，不联网） =================

def guess_kind(pe_raw: str, year_high_raw: str, tx_code: str) -> str:
    """从行情段位判标的类型，不依赖外部字典。

    **只对沪深北代码有效**：判据里的「52 周高/低恒为 -1」是 A 股段位特性，
    港股 / 美股同位段语义不同（实测 hkHSI、usIXIC 会被误判成基金），
    所以非 sh/sz/bj 前缀直接返回空串 —— 宁可不标类型，也不给错的类型。
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


def parse_quote(parts: list, market: str, tx_code: str = "") -> dict:
    """腾讯 `~` 分隔字段解析。A股 88 段 / 北交所 87 段 / 港股 78 段 / 美股 71 段。

    tx_code 是带市场前缀的请求代码（sh000905）。段位里的 parts[2] 只有裸代码
    （000905），判类型时区分不出沪深，所以类型判定必须靠 tx_code。
    """
    if len(parts) < 40:
        return {}
    pe_raw = (parts[39] or "").strip()
    year_high = (parts[47] or "").strip() if len(parts) > 47 else ""
    a_share = market in ("A_SHARE", "BJ_SHARE")
    q = {
        "name": parts[1], "code": parts[2],
        "price": _f(parts[3]), "last_close": _f(parts[4]), "open": _f(parts[5]),
        "chg": _f(parts[31]), "pct_chg": _f(parts[32]),
        "high": _f(parts[33]), "low": _f(parts[34]),
        "amount": _f(parts[37]),                       # A股 万元；港美股 元
        "amount_unit": "万元" if a_share else "元",
        # p[38] 换手率% / p[43] 振幅%（= (高-低)/昨收*100，已用四只标的核对一致）。
        # 港股 p[38] 恒为 0、美股为空串 —— 只在 A 股/北交所语义确定，其余给 None
        # 而不是 0：0 会被当成「今天没换手」，那是错的数字，比缺失更贵。
        "turnover": _f(parts[38]) if a_share and len(parts) > 38 else None,
        "amplitude": _f(parts[43]) if len(parts) > 43 else None,
        "pe": _f(parts[39]),
        "float_mv_yi": _f(parts[44]) if len(parts) > 44 else 0.0,   # 流通市值(亿)
        "mv_yi": _f(parts[45]) if len(parts) > 45 else 0.0,         # 总市值(亿)
        "update_time": parts[30],
        "_pe_raw": pe_raw, "_year_high_raw": year_high,
    }
    # 当日是否真的在交易。沪市指数在未开盘时依然返回昨收价，只是没有成交也没有
    # 当日高低价 —— 撞号消歧就靠这个判据，不能用「报价是否为空」。
    q["alive"] = q["amount"] > 0 or (q["high"] > 0 and q["low"] > 0)
    q["kind"] = guess_kind(pe_raw, year_high, tx_code)
    return q


# ================= 联网取报价 =================

def _http_gbk(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("gbk", errors="ignore")


def fetch_quotes(tx_codes, timeout: int = 8):
    """批量取腾讯报价。返回 {tx_code: quote}；**取数失败返回 None**。

    三个出口必须分清（`{}` 兼表两种含义就会把「接口挂了」误报成「无此代码」）：
      None  取数失败（网络异常 / 接口变更），调用方不得据此下任何结论
      {}    请求成功但一个都没匹配上 —— 这些代码确实不存在
      {...} 命中的部分。**不存在的标的腾讯直接不返回该行**（既不是空串也不
            报错），所以「传 3 个回 1 个」是正常的，缺的那几个就是不存在

    代码大小写必须与请求一致（全小写 usaapl 会整体返回 v_pv_none_match）。
    """
    codes = [c for c in dict.fromkeys(str(c or "") for c in tx_codes) if c]
    if not codes:
        return {}
    out, ok_any = {}, False
    for i in range(0, len(codes), _BATCH):
        batch = codes[i:i + _BATCH]
        try:
            txt = _http_gbk(_QUOTE_URL + ",".join(batch), timeout=timeout)
        except Exception:
            continue                       # 单批失败不连坐其它批
        ok_any = True
        for m in re.finditer(r'v_([a-zA-Z0-9\.\_]+)="([^"]*)"', txt):
            code, body = m.group(1), m.group(2)
            if not body or code not in batch:
                continue
            market = market_of(code)
            q = parse_quote(body.split("~"), market, code)
            if q and q["price"] > 0:
                q["tx_code"], q["market"] = code, market
                out[code] = q
    return out if ok_any else None


# ================= 撞号消歧 =================

def is_major_sh_index(tx_code: str) -> bool:
    """是否为白名单里的沪市宽基指数。

    **刻意不看 kind**：kind 由 guess_kind 从 PE 段推断，PE 段偶发为空时
    sh000905 会被判成「债券 / 其他」，跟着连宽基身份一起丢掉。代码已在白名单、
    前缀又是 sh，身份就已确定，不该再让一个会抖动的推断值来否决它。
    """
    c = str(tx_code or "").lower()
    return c[:2] == "sh" and c[2:] in MAJOR_SH_INDEX


def _prior_rank(q: dict) -> int:
    """同样活跃时的取舍先验，数字越小越优先。"""
    code = str(q.get("tx_code", ""))
    if code.startswith("sh") and q.get("kind") == "指数":
        return 0 if code[2:] in MAJOR_SH_INDEX else 2
    return 1                              # 个股 / 基金 / 北交所 / 深市指数


def rank_key(q: dict) -> tuple:
    """撞号排序键：白名单宽基指数 → 当日是否在交易 → 类型先验 → 成交额。

    两条判据的顺序是权衡过的，反过来都会出静默取错：

    · **白名单宽基排在活跃度之前**。用户输 000905 想看中证500 的概率远高于
      深市厦门港务，而「沪市那只不存在」这种情况腾讯本来就直接不返回该行、
      根本进不了候选。真正的风险是接口偶发对指数返回 amount=0 —— 若让活跃度
      压过白名单，那一刻 000905 就会静默滑向厦门港务，图能画、涨跌幅有数字。
    · **活跃度仍是非白名单撞号的第一判据**。沪市有些同号标的返回昨收但久无
      成交，「取到报价」不等于「取对标的」，这时得让位给真正在交易的那只。
    """
    return (0 if is_major_sh_index(q.get("tx_code")) else 1,
            0 if q.get("alive") else 1,
            _prior_rank(q),
            -_f(q.get("amount")))


def resolve_candidates(raw, timeout: int = 8):
    """把输入解析成**按活跃度排序**的候选列表。**取数失败返回 None**。

    返回 [quote, ...]（每个 quote 里带 tx_code / market / kind / alive）：
      None  联网失败，调用方必须显示「取数失败」而不是「无此代码」
      []    请求成功但沪深北都没有这个代码
      len>1 确实撞号（000001 = 上证指数 + 平安银行，两边都活跃）
            → 调用方**必须**把选择权交还用户或显式标注取了哪个市场

    仅接受 6 位裸数字与带 sh/sz/bj 前缀的代码；港股 / 美股 / 商品请走
    page_live_quote.resolve_symbol（本模块只解决撞号这一件事）。
    """
    s = str(raw or "").strip().upper().split(".")[0]
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", s):          # 已显式指定市场，无歧义
        got = fetch_quotes([s.lower()], timeout=timeout)
        if got is None:
            return None
        return list(got.values())
    if not re.fullmatch(r"\d{6}", s):
        return []
    got = fetch_quotes([f"{p}{s}" for p in prefixes_for(s)], timeout=timeout)
    if got is None:
        return None
    return sorted(got.values(), key=rank_key)


def is_ambiguous(cands: list) -> bool:
    """沪深北是否存在多个同号标的。

    **判据刻意只看候选个数，不看 alive**。2026-09-03 09:15 实测：开盘前全市场
    amount=0、high/low=0，alive 一律 False；若要求「另一只也在交易」才算撞号，
    盘前就一条都标不出来 —— 而撞号是结构事实，跟当刻有没有成交无关。
    腾讯对不存在的标的直接不返回该行，所以「候选 > 1」本身已是充分证据。
    """
    return len(cands) > 1


def describe_alternatives(cands: list) -> list:
    """把除首选之外的候选描述成「市场+类型·名称」，供 UI 与推送文案标注。"""
    return [f"{market_label(q.get('tx_code'))}{q.get('kind', '')}·{q.get('name', '')}"
            for q in cands[1:]]


def resolve(raw, timeout: int = 8):
    """取**最可能**的那一个候选。**取数失败返回 None，无此代码返回 {}**。

    结果里附 `ambiguous`（沪深北是否还有别的同号标的）与 `alternatives`
    （其余候选的「市场+名称」，供 UI 或推送文案标注）。调用方**不得**忽略
    ambiguous 就直接展示 —— 那就退回成静默取错了。
    """
    cands = resolve_candidates(raw, timeout=timeout)
    if cands is None:
        return None
    if not cands:
        return {}
    best = dict(cands[0])
    best["ambiguous"] = is_ambiguous(cands)
    best["alternatives"] = describe_alternatives(cands)
    return best


def market_label(tx_code: str) -> str:
    return {"sh": "沪市", "sz": "深市", "bj": "北交所",
            "hk": "港股", "us": "美股"}.get(str(tx_code or "")[:2].lower(), "")


def kline_url(tx_code: str, limit: int = 260, period: str = "day",
              adjust: str = "qfq") -> str:
    """前复权 K 线 URL。北交所自动切 newfqkline（老端点对 bj 只返回 1 根）。

    limit 上限 800：>=801 会**静默退回 640 根**，>=3000 直接返回 0 根。
    """
    ep = KLINE_EP.get(market_of(tx_code), "fqkline")
    return (f"https://web.ifzq.gtimg.cn/appstock/app/{ep}/get"
            f"?param={tx_code},{period},,,{min(int(limit), 800)},{adjust}")


def xueqiu_url(tx_code: str) -> str:
    """雪球个股页。前缀必须取自解析结果，不能再按 6/5/9 猜 —— 猜错就跳到
    另一只同号标的的页面（000905 会跳到厦门港务）。"""
    pfx = str(tx_code or "")[:2].upper()
    return f"https://xueqiu.com/S/{pfx}{str(tx_code)[2:]}" if pfx else ""

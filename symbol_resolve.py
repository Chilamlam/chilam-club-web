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

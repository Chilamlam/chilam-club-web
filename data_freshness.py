# -*- coding: utf-8 -*-
"""数据新鲜度判定（计算层：不 import streamlit、不联网、可脱 runtime 单测）。

为什么要有这一层
----------------
2026-09-01 的事故：RPS 跑批连续三班扑空（两班过零点锚到未收盘的当天、一班被
题材接口拖到超时被杀），`data/strong_stocks.csv` 停在 08-31，而同一批的突破池、
连板天梯、收盘摘要全是 09-01 的新数据。前端对此**没有任何提示**——用户看到的
是一份看起来正常、实际过期一天的榜单。这比页面报错危险得多。

判定思路
--------
两条独立判据，任一命中即视为过期：

1. **同批比对**：本产物日期 < 站内参考日期（取同批产物里最新的那个）。
   这条最灵敏——同一次跑批的产物本该同日，不同日必然意味着某一步失败了。
2. **日历比对**：本产物日期距今超过 `max_lag_days` 个自然日。
   兜住「整批产物一起过期」的情况，此时同批比对会全部一致而失效。

收盘更新窗口
------------
publication_status 模块使用跑批保存的交易日历区分休市和更新延迟，前端无需联网。
日历缺失或过期时明确标注按工作日估算。verdict 保留原有的同批比对与自然日
兜底规则，供现有榜单调用。
"""
from __future__ import annotations

import datetime as dt
import re

# 允许的最大滞后自然日数。周末两天 + 1 天缓冲 + 判定当天 = 4。
MAX_LAG_DAYS = 4


def norm_date(v) -> str:
    """把 20260901 / 2026-09-01 / 2026-09-01 22:05:08 统一压成 YYYYMMDD。

    返回空串表示取不出日期——调用方必须区分「没有日期戳」与「日期是旧的」，
    前者是数据格式问题，后者是跑批问题，处置方式完全不同。
    """
    text = "" if v is None else str(v).strip()
    match = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})(?=$|[ T]|\.0+$)", text)
    if not match:
        return ""
    digits = "".join(match.groups())
    return digits if _to_date(digits) is not None else ""


def _to_date(s: str):
    try:
        return dt.datetime.strptime(s, "%Y%m%d").date()
    except Exception:  # noqa: BLE001
        return None


def verdict(item_date, reference_date=None, today=None,
            max_lag_days: int = MAX_LAG_DAYS) -> dict:
    """判定单个产物的新鲜度。

    参数
    ----
    item_date       本产物的日期戳（任意上述格式）
    reference_date  同批产物里最新的日期戳；None 表示不做同批比对
    today           判定基准日（date 对象），None 取北京时间当天

    返回 dict，关键字段：
        status  "ok" | "stale" | "unknown"
        date    归一化后的 YYYYMMDD，取不出时为空串
        reason  过期原因（人话），status == "ok" 时为空串
        lag_days 距今自然日数，取不出日期时为 None

    "unknown" 与 "stale" 必须分开：前者说明产物缺日期列（要修产物），
    后者说明跑批没跑成（要补跑）。合并成一个状态会让人修错东西。
    """
    d = norm_date(item_date)
    if not d:
        return {"status": "unknown", "date": "", "reason": "产物缺少有效日期戳（缺失或日期格式错误）",
                "lag_days": None}

    base = today or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    dd = _to_date(d)
    lag = (base - dd).days if dd else None
    if lag is not None and lag < 0:
        return {"status": "unknown", "date": d, "lag_days": lag,
                "reason": f"本表数据日期 {_fmt(d)} 晚于北京时间判定日 {_fmt(base.strftime('%Y%m%d'))}，请检查数据日期"}

    ref = norm_date(reference_date) if reference_date else ""
    if ref and _to_date(ref) > base:
        ref = ""
    if ref and d < ref:
        return {"status": "stale", "date": d, "lag_days": lag,
                "reason": f"本表数据日期 {_fmt(d)}，而站内其他榜单已更新到 "
                          f"{_fmt(ref)}，说明这一步的跑批没有成功"}

    if lag is not None and lag > max_lag_days:
        return {"status": "stale", "date": d, "lag_days": lag,
                "reason": f"本表数据日期 {_fmt(d)}，距今已 {lag} 天"}

    return {"status": "ok", "date": d, "lag_days": lag, "reason": ""}


def _fmt(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d


def pick_date(df, candidates=("更新日期", "update_date", "date")) -> str:
    """从 DataFrame 里取日期戳，按候选列名依次尝试，取最后一行。

    三张榜单的日期列名不统一（RPS 用中文「更新日期」、突破池用 update_date），
    写死单一列名会静默返回空串，让「日期列改名」伪装成「没有日期」。
    """
    if df is None or len(df) == 0:
        return ""
    for col in candidates:
        if col in getattr(df, "columns", []):
            return norm_date(df[col].iloc[-1])
    return ""

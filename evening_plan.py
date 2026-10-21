# -*- coding: utf-8 -*-
"""
晚间复盘与次日交易计划计算层（纯 stdlib + pandas + numpy，禁止 import streamlit / 禁止联网）

设计目标
--------
将《181篇A股短线投机方法论》中可量化、可验证的规则沉淀为每日收盘后自动运行的客观计算引擎：
  1. 情绪周期与日历特殊法则识别（097双冰强反 / 081退潮D3陷阱 / 052阳极阴生 / 065中位股亏钱扩散 / 121&145周五定律 / 027节后定律）
  2. 125「10:00前硬核首板 vs 10:00后首板」次日晋级率与溢价对比
  3. 六大晚间客观选股池构建：
     - pool_10am_first      : #125 10:00前硬核首板池（叠 #030 低价小市值排序）
     - pool_2b_pk           : #033/#071 换手二板池 & #049 多龙同台《中庸》观察组
     - pool_ice_rebound     : #080/#118 冰点「狗啃板」（烂而不弱）与 #035 炸板反包池
     - pool_ma6_pullback    : #128 人气龙头首阴回踩 MA6 / MA12 观察池
     - pool_rubbing_breakout: #169 揉搓线洗盘 & #139/#172 突破回踩池
     - pool_trend_catchup   : #077/#103 强趋势板块低位首板补涨池
  4. 四道防雷质检门（#096 次新/活跃股死亡换手率、#176 高位双针滞涨、#004 一字筹码断层、#032 午后烂板/弱股性）
  5. 自选股战法共振与排雷诊断（供 VIP 收盘推送与「我的池子」联动）
  6. 战法历史表现回看（基于真实归档统计 10:00前首板 vs 10:00后首板、换手二板、不同情绪周期下的次日晋级率与超额表现）
  7. 次日盘中 5 大时间节点观察沙盘

铁律：
  - 纯计算层：不 import streamlit，不发起任何网络请求，不调用 LLM。
  - 缺数如实留空（None / "—"），绝不用 0 填补缺失。
  - 合规边界：所有输出仅描述客观盘面结构、历史统计规律与观察条件，不出现任何正向荐股或仓位指令。
"""
from __future__ import annotations

import datetime
import json
import os
import re

import numpy as np
import pandas as pd

EVENING_DIR = os.path.join("data", "evening_plan")
HIST_DIR = os.path.join(EVENING_DIR, "history")
LATEST_PATH = os.path.join(EVENING_DIR, "latest.json")
METHODOLOGY_KB_PATH = os.path.join("data", "methodology_181.json")

LADDER_PATH = os.path.join("data", "limit_ladder.json")
DERIVED_PATH = os.path.join("data", "sentiment", "derived.json")
LADDER_HIST_PATH = os.path.join("data", "sentiment", "ladder_history.csv")
BREADTH_HIST_PATH = os.path.join("data", "sentiment", "breadth_history.csv")
SNAPSHOT_CSV = os.path.join("data", "market_snapshot.csv")
STRONG_CSV = os.path.join("data", "strong_stocks.csv")
BREAKOUT_CSV = os.path.join("data", "breakout_stocks.csv")
PRICES_CSV = os.path.join("data", "scorecard", "prices.csv")

DISCLAIMER = (
    "本模块基于公开历史行情与客观规则做结构化分类与复盘辅助，"
    "所有战法池、均线距离与风险标签仅供盘面观察，不构成任何投资建议或交易依据。"
)

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


_BJ_PREFIXES = ("92", "43", "83", "87", "88", "899")


def _bare(code) -> str:
    s = str(code or "").strip().upper().split(".")[0]
    m = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", s)
    return m.group(2) if m else s


def is_bj_stock(code) -> bool:
    """判定是否为北交所/三板标的（92/43/83/87/88/899 开头，或带 .BJ / bj 前缀）。

    战法选股池仅聚焦沪深 A 股（主板/创业板/科创板），北交所 30% 涨跌停制度与流动性结构不同，一律不入池。
    """
    raw = str(code or "").strip().upper()
    if not raw:
        return False
    if raw.endswith(".BJ") or raw.startswith("BJ"):
        return True
    b = _bare(raw)
    return bool(b and b.startswith(_BJ_PREFIXES))


def _fmt_date(d: str) -> str:
    digits = re.sub(r"\D", "", str(d or ""))[:8]
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(d or "—")


def _norm_date(d: str) -> str:
    return re.sub(r"\D", "", str(d or ""))[:8]


def parse_zbc_and_seal(reason: str | None) -> tuple[int | None, float | None]:
    """从涨停池 reason 字段（如 '封板资金 1.12 亿 | 炸板 4 次'）提取 (炸板次数, 封板资金亿)。

    若字符串中未包含对应字段则返回 None，绝不凭空猜 0。
    """
    if not reason:
        return None, None
    s = str(reason)
    zbc = None
    seal_yi = None
    m_zbc = re.search(r"炸板\s*(\d+)\s*次", s)
    if m_zbc:
        zbc = int(m_zbc.group(1))
    m_seal = re.search(r"封板资金\s*([0-9]+(?:\.[0-9]+)?)\s*亿", s)
    if m_seal:
        seal_yi = float(m_seal.group(1))
    return zbc, seal_yi


def _parse_time_minutes(first_time: str | None) -> int | None:
    """将 '09:35:12' 转为自 00:00 起的分钟数（如 9*60+35=575），无法解析返回 None。"""
    if not first_time:
        return None
    s = str(first_time).strip()
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if 0 <= hh < 24 and 0 <= mm < 60:
        return hh * 60 + mm
    return None


def is_pre_10am(first_time: str | None) -> bool:
    """判定是否为 10:00:00 及之前封板（#125 十点前涨停）。"""
    if not first_time:
        return False
    s = str(first_time).strip()
    if re.match(r"^09:\d{2}(:\d{2})?$", s):
        return True
    if re.match(r"^10:00(:00)?$", s):
        return True
    return False


# ================= 1. 10点前首板 vs 10点后首板溢价对比 (#125) =================

def first_board_time_split(
    ladder_hist: pd.DataFrame | None,
    date_key: str,
    prev_date: str | None,
    pct_map: dict | None = None,
) -> dict:
    """统计 prev_date 首板中「10:00前封板」与「10:00后封板」在 date_key 的晋级率与涨幅表现（#125）。"""
    dk = _norm_date(date_key)
    pk = _norm_date(prev_date or "")
    if ladder_hist is None or ladder_hist.empty or not pk or not dk:
        return {"status": "failed", "reason": "缺少涨停历史归档或对比日期"}

    h = ladder_hist.copy()
    h = h[~h["code"].astype(str).map(is_bj_stock)]
    h["date"] = h["date"].astype(str).map(_norm_date)
    prev_1b = h[(h["date"] == pk) & (h["limit_times"] == 1)].copy()
    cur_all = h[h["date"] == dk]
    if prev_1b.empty or cur_all.empty:
        return {"status": "failed", "reason": f"缺少 {pk} 首板或 {dk} 涨停归档"}

    cur_map = dict(zip(cur_all["code"].astype(str).map(_bare), cur_all["limit_times"]))
    pct_lookup = {_bare(k): float(v) for k, v in (pct_map or {}).items() if v is not None}

    def _calc_bucket(df_sub: pd.DataFrame, label: str) -> dict:
        base = int(len(df_sub))
        if base == 0:
            return {
                "label": label, "base": 0, "promoted": 0, "promo_rate": None,
                "median_pct": None, "win_rate": None, "sample_pct": 0,
            }
        codes = [_bare(c) for c in df_sub["code"].astype(str)]
        promoted = sum(1 for c in codes if cur_map.get(c, 0) >= 2)
        pcts = [pct_lookup[c] for c in codes if c in pct_lookup]
        s_pct = pd.Series(pcts, dtype="float64") if pcts else pd.Series(dtype="float64")
        return {
            "label": label,
            "base": base,
            "promoted": int(promoted),
            "promo_rate": float(promoted / base),
            "median_pct": float(s_pct.median()) if not s_pct.empty else None,
            "win_rate": float((s_pct > 0).mean()) if not s_pct.empty else None,
            "sample_pct": int(len(pcts)),
        }

    mask_pre = prev_1b["first_time"].map(is_pre_10am)
    pre_bucket = _calc_bucket(prev_1b[mask_pre], "10:00前首板（#125硬核首板）")
    post_bucket = _calc_bucket(prev_1b[~mask_pre], "10:00后首板（盘中/午后板）")

    diff_promo = None
    if pre_bucket["promo_rate"] is not None and post_bucket["promo_rate"] is not None:
        diff_promo = float(pre_bucket["promo_rate"] - post_bucket["promo_rate"])

    diff_med = None
    if pre_bucket["median_pct"] is not None and post_bucket["median_pct"] is not None:
        diff_med = float(pre_bucket["median_pct"] - post_bucket["median_pct"])

    verdict = "昨日首板分时溢价对比："
    if pre_bucket["promo_rate"] is not None and post_bucket["promo_rate"] is not None:
        verdict += (
            f"10:00前首板 1进2 晋级率 {pre_bucket['promo_rate']*100:.1f}% "
            f"({pre_bucket['promoted']}/{pre_bucket['base']}) vs "
            f"10:00后首板 {post_bucket['promo_rate']*100:.1f}% "
            f"({post_bucket['promoted']}/{post_bucket['base']})"
        )
    if pre_bucket["median_pct"] is not None and post_bucket["median_pct"] is not None:
        verdict += (
            f"；次日中位涨幅 {pre_bucket['median_pct']:+.2f}% vs {post_bucket['median_pct']:+.2f}%"
        )

    return {
        "status": "ok",
        "prev_date": pk,
        "date": dk,
        "pre_10am": pre_bucket,
        "post_10am": post_bucket,
        "promo_rate_spread": diff_promo,
        "median_pct_spread": diff_med,
        "verdict": verdict,
        "rule_ref": "#125 十点前涨停",
    }


# ================= 2. 中位股亏钱效应扩散监控 (#063 / #065) =================

def mid_tier_loss_diffusion(
    ladder_hist: pd.DataFrame | None,
    date_key: str,
    prev_date: str | None,
    pct_map: dict | None = None,
) -> dict:
    """监控昨日中位连板股（3~4板）在今日的大面率与中位表现（#063 高中低位划分 / #065 亏钱效应扩散）。"""
    dk = _norm_date(date_key)
    pk = _norm_date(prev_date or "")
    if ladder_hist is None or ladder_hist.empty or not pk:
        return {"status": "failed", "reason": "缺少涨停归档"}

    h = ladder_hist.copy()
    h = h[~h["code"].astype(str).map(is_bj_stock)]
    h["date"] = h["date"].astype(str).map(_norm_date)
    prev_df = h[h["date"] == pk]
    cur_df = h[h["date"] == dk]
    if prev_df.empty:
        return {"status": "failed", "reason": f"缺少 {pk} 涨停归档"}

    max_h_today = int(cur_df["limit_times"].max()) if not cur_df.empty else int(prev_df["limit_times"].max())
    # 动态中位定义（#063）：默认 3~4 板；若最高板 >= 7，则中位含 3~5 板
    mid_low, mid_high = 3, max(4, min(5, max_h_today - 1)) if max_h_today >= 5 else 4
    mid_prev = prev_df[(prev_df["limit_times"] >= mid_low) & (prev_df["limit_times"] <= mid_high)]

    tier_def = {
        "high_tier": f"≥{max(4, max_h_today - 1)}板" if max_h_today >= 4 else f"{max_h_today}板",
        "mid_tier": f"{mid_low}~{mid_high}板",
        "low_tier": "1~2板",
    }

    if mid_prev.empty:
        return {
            "status": "ok",
            "prev_date": pk,
            "date": dk,
            "tier_definition": tier_def,
            "mid_n": 0,
            "big_loss_count": 0,
            "big_loss_rate": None,
            "median_pct": None,
            "warning": False,
            "verdict": f"昨日无 {mid_low}~{mid_high} 板中位标的",
            "rule_ref": "#063/#065 中位股亏钱扩散",
        }

    pct_lookup = {_bare(k): float(v) for k, v in (pct_map or {}).items() if v is not None}
    cur_map = dict(zip(cur_df["code"].astype(str).map(_bare), cur_df["limit_times"])) if not cur_df.empty else {}

    records = []
    for _, row in mid_prev.iterrows():
        c = _bare(row["code"])
        pct = pct_lookup.get(c)
        promoted = cur_map.get(c, 0) >= int(row["limit_times"]) + 1
        records.append({
            "code": c,
            "name": str(row.get("name") or c),
            "prev_boards": int(row["limit_times"]),
            "pct_chg": pct,
            "promoted": bool(promoted),
        })

    valid_pcts = [r["pct_chg"] for r in records if r["pct_chg"] is not None]
    promoted_cnt = sum(1 for r in records if r["promoted"])
    mid_n = len(records)

    if not valid_pcts:
        # 即使无全市场涨幅，也可看晋级失败比例
        fail_rate = float((mid_n - promoted_cnt) / mid_n)
        return {
            "status": "partial",
            "prev_date": pk,
            "date": dk,
            "tier_definition": tier_def,
            "mid_n": mid_n,
            "promoted_count": promoted_cnt,
            "big_loss_count": None,
            "big_loss_rate": None,
            "median_pct": None,
            "warning": bool(promoted_cnt == 0 and mid_n >= 3),
            "stocks": records,
            "verdict": f"昨日中位股（{mid_low}~{mid_high}板）共 {mid_n} 只，今日晋级 {promoted_cnt} 只（无当日涨跌幅明细）",
            "rule_ref": "#063/#065 中位股亏钱扩散",
        }

    s = pd.Series(valid_pcts, dtype="float64")
    big_loss = int((s <= -5.0).sum())
    loss_rate = float(big_loss / len(valid_pcts))
    med_pct = float(s.median())
    warning = bool(loss_rate >= 0.40 or (med_pct <= -4.0 and len(valid_pcts) >= 2))

    verdict = (
        f"昨日中位股（{mid_low}~{mid_high}板）{len(valid_pcts)} 只："
        f"今日中位涨幅 {med_pct:+.2f}%，大面（≤-5%）{big_loss} 只（占比 {loss_rate*100:.0f}%）"
    )
    if warning:
        verdict += " —— ⚠️ 触发 #065 中位股亏钱效应扩散警戒，谨慎接力中位跟风"
    else:
        verdict += " —— 中位股承接尚属温和"

    return {
        "status": "ok",
        "prev_date": pk,
        "date": dk,
        "tier_definition": tier_def,
        "mid_n": int(len(valid_pcts)),
        "promoted_count": int(promoted_cnt),
        "big_loss_count": big_loss,
        "big_loss_rate": loss_rate,
        "median_pct": med_pct,
        "warning": warning,
        "stocks": records,
        "verdict": verdict,
        "rule_ref": "#063/#065 中位股亏钱扩散",
    }


# ================= 3. 情绪周期与日历特殊法则引擎 =================

def detect_cycle_and_calendar_laws(
    trade_date: str,
    derived: dict | None,
    ladder_hist: pd.DataFrame | None = None,
    breadth_hist: pd.DataFrame | None = None,
    pct_map: dict | None = None,
) -> dict:
    """识别当日触发的周期与时间日历特殊法则（#097/#158/#081/#052/#056/#086/#121/#145/#027/#100）。"""
    dk = _norm_date(trade_date)
    prev_date = _norm_date((derived or {}).get("prev_date") or "")
    phase_obj = (derived or {}).get("phase") or {}
    cur_phase = phase_obj.get("phase") if phase_obj.get("status") == "ok" else "未定"
    th = (derived or {}).get("phase_thresholds") or phase_obj.get("thresholds") or {}

    # 1. 星期与节后日历判定
    weekday_idx = None
    weekday_label = "—"
    gap_days = 1
    if len(dk) == 8:
        try:
            dt = datetime.datetime.strptime(dk, "%Y%m%d")
            weekday_idx = dt.weekday()
            weekday_label = WEEKDAY_NAMES[weekday_idx]
            if len(prev_date) == 8:
                dt_prev = datetime.datetime.strptime(prev_date, "%Y%m%d")
                gap_days = (dt - dt_prev).days
        except ValueError:
            pass

    # 2. 重建近 5 日情绪周期序列（用于判定 #097 双冰强反 与 #081 退潮 D3 陷阱）
    recent_phases: list[dict] = []
    if ladder_hist is not None and not ladder_hist.empty:
        import sentiment as sm
        h = ladder_hist.copy()
        h["date"] = h["date"].astype(str).map(_norm_date)
        h = h[h["date"] <= dk]
        s_r12 = sm.promotion_rate_series(h, before_date=None)
        if not s_r12.empty:
            for d_idx, r_val in s_r12.iloc[-5:].items():
                p_name = sm.classify_phase(float(r_val), th)
                recent_phases.append({
                    "date": str(d_idx),
                    "r12": float(r_val),
                    "phase": p_name,
                })

    active_laws: list[dict] = []

    # 法则 A：#097 双冰强反 vs 单冰弱修
    if len(recent_phases) >= 2:
        p_today = recent_phases[-1]["phase"]
        p_yest = recent_phases[-2]["phase"]
        p_prev2 = recent_phases[-3]["phase"] if len(recent_phases) >= 3 else ""
        if p_today == "冰点期" and p_yest == "冰点期":
            active_laws.append({
                "id": "097",
                "title": "双冰强反黄金节点（#097/#158）",
                "level": "opportunity",
                "summary": "连续两日处于冰点期（双冰共振），恐慌盘充分宣泄，次日极高概率迎来全市场强修复，重点观察今日逆势封板的「狗啃板」与新题材10:00前首板。",
            })
        elif p_today == "冰点期" and p_prev2 == "冰点期" and p_yest in ("退潮期", "分歧期"):
            active_laws.append({
                "id": "097",
                "title": "冰点二次回踩共振（#097 双冰变体）",
                "level": "opportunity",
                "summary": "经历「冰点→弱修→再冰点」双底结构，次日情绪具备强反弹动能，关注分歧转一致的前排卡位龙头。",
            })
        elif p_today == "冰点期":
            active_laws.append({
                "id": "158",
                "title": "首冰试错节点（#097/#158 单冰防弱修）",
                "level": "info",
                "summary": "今日首次进入冰点期：单冰次日往往表现为局部弱修复（冲高易回落），观察重点在于淘汰赛中幸存的「烂而不弱」独苗板（#080）。",
            })

        # 法则 B：#081 退潮期第三天（D3）陷阱
        # 若前日为冰点(D1)，今日为弱修复至退潮/分歧(D2)，则明日进入 D3！
        if p_prev2 == "冰点期" and p_yest in ("冰点期", "退潮期") and p_today in ("退潮期", "分歧期"):
            active_laws.append({
                "id": "081",
                "title": "退潮期 D3 节点预警（#081 周期的秘密）",
                "level": "warning",
                "summary": "前日冰点（D1）→ 今日修复过渡（D2），明日将进入退潮周期第 3 天（D3）。按 #081 铁律：D3 极易出现修复乏力后的二次大面杀跌，次日严禁追高缩量高标。",
            })

    # 法则 C：#052/#056/#069 高潮阳极阴生（做两头弃中间）
    gap_info = (derived or {}).get("ladder_gap") or {}
    zt_total = int(gap_info.get("total") or 0)
    if cur_phase == "发酵期" or zt_total >= 80:
        active_laws.append({
            "id": "052",
            "title": "阳极阴生·做两头弃中间（#052/#056/#069）",
            "level": "warning",
            "summary": (
                f"当前处于情绪高涨期（涨停 {zt_total} 家，周期「{cur_phase}」）。"
                "高潮次日必迎分化：观察方向宜「做两头、弃中间」——仅盯绝对最高标/10:00前最强1进2，"
                "或新分支低位首板，回避 3~4 板中位跟风与缩量抢跑一字。"
            ),
        })

    # 法则 D：#086 / #121 / #145 周四、周五与周一/节后日历效应
    if weekday_idx == 4:  # 周五
        active_laws.append({
            "id": "145",
            "title": "周五换手二板空间龙定律（#145/#086）",
            "level": "opportunity",
            "summary": "今日为周五：历史统计表明，新周期空间龙最高概率诞生于「周五的换手二板」，其次是「周五的10:00前首板」；同时注意周末消息面发酵方向。",
        })
        active_laws.append({
            "id": "121",
            "title": "周五高位空间板分歧效应（#121）",
            "level": "warning",
            "summary": "周五资金天然规避周末监管与不确定性，4板及以上高位股极易出现筹码松动或断板，对高标保持防守视角。",
        })
    elif weekday_idx == 3:  # 周四
        active_laws.append({
            "id": "121",
            "title": "周四冲高防周五高标断板（#121）",
            "level": "info",
            "summary": "今日为周四，明日周五通常是高标分歧兑现节点，晚间复盘对高位连板需提高换手承接要求。",
        })
    elif weekday_idx == 0 or gap_days >= 4:  # 周一或节后
        active_laws.append({
            "id": "027",
            "title": "周末/节后首日开盘反向定律（#027）",
            "level": "info",
            "summary": "经过假期舆情发酵后：若发酵题材大幅高开一致抢跑，盘中极易高开低走兑现；若核心标的分歧低开且9:35后承接有力，反而易低开高走。",
        })

    # 法则 E：#065 中位股亏钱扩散 & #125 首板分时溢价
    mid_loss = mid_tier_loss_diffusion(ladder_hist, dk, prev_date, pct_map)
    if mid_loss.get("warning"):
        active_laws.append({
            "id": "065",
            "title": "中位股（3~4板）亏钱效应扩散预警（#065）",
            "level": "warning",
            "summary": mid_loss.get("verdict", "昨日中位连板股今日出现明显亏钱效应，警惕向全市场传导。"),
        })

    split_125 = first_board_time_split(ladder_hist, dk, prev_date, pct_map)

    # 法则 F：#089/#090 跌停潮与流动性观察
    if breadth_hist is not None and not breadth_hist.empty:
        bh = breadth_hist.copy()
        bh["date"] = bh["date"].astype(str).map(_norm_date)
        bh = bh[bh["date"] <= dk].sort_values("date")
        if not bh.empty:
            cur_ld = int(bh.iloc[-1].get("limit_down") or 0)
            prev_ld = int(bh.iloc[-2].get("limit_down") or 0) if len(bh) >= 2 else 0
            if cur_ld >= 25:
                active_laws.append({
                    "id": "089",
                    "title": "跌停家数升温·流动性警戒（#089/#090）",
                    "level": "warning",
                    "summary": f"今日全市场跌停达 {cur_ld} 家（前日 {prev_ld} 家），亏钱效应未完全收敛，需等待跌停明显消退、撬板资金出现后再扩大观察范围。",
                })
            elif prev_ld >= 25 and cur_ld <= 10:
                active_laws.append({
                    "id": "158",
                    "title": "跌停潮迅速消退·恐慌衰竭信号（#090/#158）",
                    "level": "opportunity",
                    "summary": f"全市场跌停由前日 {prev_ld} 家骤降至今日 {cur_ld} 家，流动性恐慌显著缓解，短线进入超跌修复窗口。",
                })

    return {
        "trade_date": dk,
        "weekday": weekday_label,
        "gap_days": int(gap_days),
        "current_phase": cur_phase,
        "recent_phases": recent_phases,
        "active_laws": active_laws,
        "first_board_split_125": split_125,
        "mid_tier_diffusion_065": mid_loss,
    }


# ================= 4. 四道防雷质检门 (#096 / #176 / #004 / #032) =================

def check_stock_risk_gates(
    code: str,
    name: str = "",
    turnover_rate: float | None = None,
    first_time: str | None = None,
    limit_times: int | None = None,
    zbc: int | None = None,
    kline_df: pd.DataFrame | None = None,
    ladder_hist: pd.DataFrame | None = None,
) -> list[dict]:
    """对单只个股执行四道防雷质检，返回命中的风险标签列表（无风险返回 []）。"""
    bare = _bare(code)
    risks: list[dict] = []

    # 1. #096 次新与活跃股死亡换手率三道坎（60% / 66.67% / 70%）
    if turnover_rate is not None and not pd.isna(turnover_rate):
        tr = float(turnover_rate)
        if tr >= 70.0:
            risks.append({
                "rule_id": "096",
                "tag": f"🚨 换手极值 {tr:.1f}%（#096 ≥70%高危区）",
                "level": "high",
                "detail": "单日换手率突破70%极值，筹码高度派发，历史统计次日承接极度脆弱。",
            })
        elif tr >= 66.67:
            risks.append({
                "rule_id": "096",
                "tag": f"⚠️ 换手过高 {tr:.1f}%（#096 ≥66.7%警戒）",
                "level": "medium",
                "detail": "单日换手率跨过66.67%二道坎，短线博弈筹码过热。",
            })
        elif tr >= 60.0:
            risks.append({
                "rule_id": "096",
                "tag": f"⚠️ 高换手 {tr:.1f}%（#096 ≥60%一道坎）",
                "level": "low",
                "detail": "单日换手率达60%以上，次日需严格观察竞价承接力度。",
            })

    # 2. #004 一字板筹码断层检测
    if limit_times is not None and int(limit_times) >= 2:
        ft = str(first_time or "")
        if ft.startswith("09:25") and (zbc is None or int(zbc) == 0):
            risks.append({
                "rule_id": "004",
                "tag": "⚠️ 一字筹码断层（#004 缺换手承接）",
                "level": "medium",
                "detail": "缩量一字晋级导致下方缺乏换手成本垫，一旦开板分歧抛压集中。",
            })

    # 3. #032 午后偷袭烂板 & 历史连板基因检测
    mins = _parse_time_minutes(first_time)
    if limit_times == 1 and mins is not None and mins >= 14 * 60 and (zbc or 0) >= 2:
        risks.append({
            "rule_id": "032",
            "tag": "⚠️ 午后频炸尾板（#032 偷袭回封）",
            "level": "medium",
            "detail": "14:00后封板且盘中反复炸板≥2次，属于非早盘主攻的被动尾板。",
        })

    if ladder_hist is not None and not ladder_hist.empty and bare:
        sub_h = ladder_hist[ladder_hist["code"].astype(str).map(_bare) == bare]
        if len(sub_h) >= 3:
            max_ever = int(sub_h["limit_times"].max())
            if max_ever == 1 and (limit_times or 1) == 1:
                risks.append({
                    "rule_id": "032",
                    "tag": f"ℹ️ 历史 {len(sub_h)} 次首板均未连板（#032 股性基因）",
                    "level": "low",
                    "detail": "近期归档中多次涨停皆止步于首板，连板接力溢价偏弱。",
                })

    # 4. #176 高位双针见顶（丄字形/双长上影滞涨）
    if kline_df is not None and len(kline_df) >= 10:
        df_k = kline_df.tail(10).copy()
        for col in ("open", "high", "low", "close"):
            if col in df_k.columns:
                df_k[col] = pd.to_numeric(df_k[col], errors="coerce").astype("float64")
        if {"open", "high", "low", "close"} <= set(df_k.columns):
            recent_5 = df_k.tail(5)
            max_10 = float(df_k["high"].max())
            cur_close = float(df_k["close"].iloc[-1])
            min_10 = float(df_k["low"].min())
            # 处于近期相对高位，且近 5 日内出现 >=2 根长上影线（上影线幅度 >= 2.8%）
            if min_10 > 0 and (max_10 - min_10) / min_10 >= 0.12 and cur_close >= min_10 * 1.06:
                upper_shadows = 0
                for _, r in recent_5.iterrows():
                    body_top = max(float(r["open"]), float(r["close"]))
                    if body_top > 0 and (float(r["high"]) - body_top) / body_top >= 0.028:
                        upper_shadows += 1
                if upper_shadows >= 2 and cur_close < max_10 * 0.985:
                    risks.append({
                        "rule_id": "176",
                        "tag": f"⚠️ 高位双上影滞涨（#176 近5日{upper_shadows}次冲高回落）",
                        "level": "medium",
                        "detail": "阶段高位连续出现长上影线且未能突破前高（丄字形双针结构），警惕派发压力。",
                    })

    return risks


# ================= 5. 六大晚间选股池计算引擎 =================

def _build_snapshot_lookup(snapshot_df: pd.DataFrame | None) -> dict[str, dict]:
    if snapshot_df is None or snapshot_df.empty:
        return {}
    out: dict[str, dict] = {}
    for _, r in snapshot_df.iterrows():
        b = _bare(r.get("ts_code"))
        if not b:
            continue
        close_v = pd.to_numeric(r.get("close"), errors="coerce")
        mv_v = pd.to_numeric(r.get("circ_mv"), errors="coerce")
        amt_v = pd.to_numeric(r.get("amount"), errors="coerce")
        out[b] = {
            "ts_code": str(r.get("ts_code") or b),
            "name": str(r.get("name") or b),
            "industry": str(r.get("industry") or "—"),
            "close": float(close_v) if pd.notna(close_v) and close_v > 0 else None,
            "circ_mv_yi": round(float(mv_v) / 10000.0, 2) if pd.notna(mv_v) and mv_v > 0 else None,
            "amount_yi": round(float(amt_v) / 100000.0, 2) if pd.notna(amt_v) and amt_v > 0 else None,
        }
    return out


def _build_turnover_lookup(
    strong_df: pd.DataFrame | None,
    breakout_df: pd.DataFrame | None,
    kline_map: dict[str, pd.DataFrame] | None = None,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for df in (strong_df, breakout_df):
        if df is None or df.empty or "turnover_rate" not in df.columns:
            continue
        for _, r in df.iterrows():
            b = _bare(r.get("ts_code"))
            tv = pd.to_numeric(r.get("turnover_rate"), errors="coerce")
            if b and pd.notna(tv) and float(tv) > 0:
                out[b] = float(tv)
    if kline_map:
        for b, kdf in kline_map.items():
            if kdf is not None and not kdf.empty and "turnover_rate" in kdf.columns:
                tv = pd.to_numeric(kdf["turnover_rate"].iloc[-1], errors="coerce")
                if pd.notna(tv) and float(tv) > 0:
                    out[_bare(b)] = float(tv)
    return out


def build_pool_10am_first(
    ladder: dict | None,
    snap_map: dict[str, dict],
    turnover_map: dict[str, float],
    ladder_hist: pd.DataFrame | None = None,
) -> dict:
    """战法池 1：#125 10:00前硬核首板池（叠 #030 低价小市值评分排序）。"""
    stocks = [s for s in ((ladder or {}).get("stocks") or []) if not is_bj_stock(s.get("code"))]
    items = []
    for s in stocks:
        lt = int(s.get("limit_times") or 1)
        if lt != 1:
            continue
        ft = str(s.get("first_time") or "").strip()
        if not is_pre_10am(ft):
            continue
        bare = _bare(s.get("code"))
        zbc, seal_yi = parse_zbc_and_seal(s.get("reason"))
        info = snap_map.get(bare, {})
        close_p = info.get("close")
        mv_yi = info.get("circ_mv_yi")
        tr = turnover_map.get(bare)

        is_one_word = ft.startswith("09:25") and (zbc == 0 or zbc is None)
        tags = ["#125 十点前首板"]
        if is_one_word:
            tags.append("#004 一字首板(观望换手)")
        else:
            tags.append("早盘换手封板")
        if close_p is not None and close_p <= 12.0:
            tags.append("#030 低价(<12元)")
        if mv_yi is not None and mv_yi <= 50.0:
            tags.append("#030 小市值(<50亿)")

        risks = check_stock_risk_gates(
            bare, s.get("name", ""), tr, ft, lt, zbc, None, ladder_hist
        )

        # 排序优先级：优先非一字换手板（9:30~10:00），其次小市值、低股价、封板早
        sort_one_word = 1 if is_one_word else 0
        sort_mv = mv_yi if mv_yi is not None else 9999.0
        sort_price = close_p if close_p is not None else 999.0

        items.append({
            "code": bare,
            "name": str(s.get("name") or info.get("name") or bare),
            "industry": str(s.get("industry") or info.get("industry") or "—"),
            "first_time": ft or "—",
            "limit_times": 1,
            "close": close_p,
            "circ_mv_yi": mv_yi,
            "seal_yi": seal_yi,
            "zbc": zbc,
            "turnover_rate": tr,
            "tags": tags,
            "risks": [r["tag"] for r in risks],
            "_sort_key": (sort_one_word, sort_mv, sort_price, ft),
        })

    items.sort(key=lambda x: x["_sort_key"])
    for it in items:
        it.pop("_sort_key", None)

    return {
        "pool_key": "pool_10am_first",
        "title": "⚡ 10:00前硬核首板池（#125 十点前涨停 × #030 小盘低价）",
        "rule_ids": ["125", "030", "020"],
        "methodology_note": (
            "依据 #125 与 #030：全天最值得跟踪的首板集中在 09:30~10:00 之间换手封死的标的"
            "（代表主力早盘做多意愿坚决、全天经受市场抛压检验），叠加流通市值越小、股价越低弹性越佳。"
        ),
        "next_day_anchor": "次日 09:25 观察是否高开（+2%~+6%佳），09:30~09:45 分歧换手不破分时均线且 10:00 前领涨同题材者为 1进2 重点观察锚点。",
        "count": len(items),
        "items": items[:25],
    }


def build_pool_2b_and_pk(
    ladder: dict | None,
    snap_map: dict[str, dict],
    turnover_map: dict[str, float],
    weekday_idx: int | None = None,
    ladder_hist: pd.DataFrame | None = None,
) -> dict:
    """战法池 2：#033/#071 换手二板定龙池 & #049 多龙同台《中庸》观察组。"""
    stocks = [s for s in ((ladder or {}).get("stocks") or []) if not is_bj_stock(s.get("code"))]
    two_boards = []
    high_groups: dict[int, list[dict]] = {}

    for s in stocks:
        lt = int(s.get("limit_times") or 1)
        if lt < 2:
            continue
        bare = _bare(s.get("code"))
        ft = str(s.get("first_time") or "").strip()
        zbc, seal_yi = parse_zbc_and_seal(s.get("reason"))
        info = snap_map.get(bare, {})
        close_p = info.get("close")
        mv_yi = info.get("circ_mv_yi")
        tr = turnover_map.get(bare)
        is_one_word = ft.startswith("09:25") and (zbc == 0 or zbc is None)

        tags = []
        if lt == 2:
            tags.append("#004 一字二板(防断层)" if is_one_word else "#033/#071 换手二板")
            if weekday_idx == 4 and not is_one_word:
                tags.append("🔥 #145 周五二板空间龙候选")
        else:
            tags.append(f"{lt}连板身位")
            if is_one_word:
                tags.append("#004 一字加速")

        risks = check_stock_risk_gates(
            bare, s.get("name", ""), tr, ft, lt, zbc, None, ladder_hist
        )

        rec = {
            "code": bare,
            "name": str(s.get("name") or info.get("name") or bare),
            "industry": str(s.get("industry") or info.get("industry") or "—"),
            "limit_times": lt,
            "first_time": ft or "—",
            "close": close_p,
            "circ_mv_yi": mv_yi,
            "seal_yi": seal_yi,
            "zbc": zbc,
            "turnover_rate": tr,
            "is_turnover_board": not is_one_word,
            "tags": tags,
            "risks": [r["tag"] for r in risks],
        }
        if lt == 2:
            two_boards.append(rec)
        else:
            high_groups.setdefault(lt, []).append(rec)

    # 换手二板优先于一字二板，其次按封板时间与小市值排序
    two_boards.sort(key=lambda x: (
        0 if x["is_turnover_board"] else 1,
        x["first_time"],
        x["circ_mv_yi"] if x["circ_mv_yi"] is not None else 9999.0,
    ))

    pk_groups = []
    for h_val in sorted(high_groups.keys(), reverse=True):
        grp = high_groups[h_val]
        grp.sort(key=lambda x: x["first_time"])
        if len(grp) >= 3:
            rule_hint = (
                f"【#049 中庸选股法】当前 {h_val} 板共有 {len(grp)} 只同身位竞争（多龙同台）："
                "次日 9:25 竞价去掉开盘最高分（防大热一致被砸）、去掉开盘最低分（防不及预期被核），"
                "重点观察开盘居中（+3%~+7%）分歧换手晋级者。"
            )
        elif len(grp) == 2:
            rule_hint = (
                f"【双龙卡位观察】当前 {h_val} 板有 2 只同身位标的，次日 9:25~9:35 对比竞价强弱与封单卡位。"
            )
        else:
            rule_hint = f"【独苗空间标】当前 {h_val} 板唯一身位，代表市场情绪高度锚点（#063）。"
        pk_groups.append({
            "height": h_val,
            "count": len(grp),
            "rule_hint": rule_hint,
            "stocks": grp,
        })

    all_items = [s for g in pk_groups for s in g["stocks"]] + two_boards
    return {
        "pool_key": "pool_2b_pk",
        "title": "🐉 换手二板定龙 & 多龙同台《中庸》池（#033 / #049 / #071 / #145）",
        "rule_ids": ["033", "049", "071", "145", "004"],
        "methodology_note": (
            "依据 #033/#071：二板是经过首板初筛后的换手定龙节点；若恰逢周五，#145 统计显示新周期空间龙最高概率出自周五换手二板。"
            "对于 ≥3 板同身位竞争组，应用 #049《中庸》法则：次日竞价去最高、去最低，观察中间温和高开分歧换手股。"
        ),
        "next_day_anchor": "次日 09:25 按同身位横向比对开盘涨幅，剔除一字顶死无换手及低开核按钮，锁定分歧换手放量晋级。",
        "count": len(all_items),
        "two_board_count": len(two_boards),
        "pk_groups": pk_groups,
        "items": all_items[:25],
    }


def build_pool_ice_rebound(
    ladder: dict | None,
    snap_map: dict[str, dict],
    turnover_map: dict[str, float],
    kline_map: dict[str, pd.DataFrame] | None = None,
    ladder_hist: pd.DataFrame | None = None,
) -> dict:
    """战法池 3：#080/#118 冰点「狗啃板」（烂而不弱）与 #035 尾盘炸板反包池。"""
    stocks = [s for s in ((ladder or {}).get("stocks") or []) if not is_bj_stock(s.get("code"))]
    # 统计今日各行业涨停家数（用于识别 #080 板块独苗）
    ind_counts: dict[str, int] = {}
    for s in stocks:
        ind = str(s.get("industry") or "—").strip()
        ind_counts[ind] = ind_counts.get(ind, 0) + 1

    items = []
    for s in stocks:
        zbc, seal_yi = parse_zbc_and_seal(s.get("reason"))
        if zbc is None or zbc < 2:
            continue
        bare = _bare(s.get("code"))
        ind = str(s.get("industry") or "—").strip()
        lt = int(s.get("limit_times") or 1)
        ft = str(s.get("first_time") or "").strip()
        info = snap_map.get(bare, {})
        close_p = info.get("close")
        mv_yi = info.get("circ_mv_yi")
        tr = turnover_map.get(bare)
        sec_cnt = ind_counts.get(ind, 1)

        tags = []
        if sec_cnt == 1:
            tags.append(f"🔥 #080 正宗狗啃板(炸{zbc}次·板块独苗)")
            priority = 0
        else:
            tags.append(f"#118 烂而不弱分歧板(炸{zbc}次回封)")
            priority = 1

        risks = check_stock_risk_gates(
            bare, s.get("name", ""), tr, ft, lt, zbc, None, ladder_hist
        )

        items.append({
            "code": bare,
            "name": str(s.get("name") or info.get("name") or bare),
            "industry": ind,
            "limit_times": lt,
            "first_time": ft or "—",
            "zbc": zbc,
            "sector_zt_count": sec_cnt,
            "close": close_p,
            "circ_mv_yi": mv_yi,
            "seal_yi": seal_yi,
            "turnover_rate": tr,
            "pattern_type": "狗啃板(板块独苗)" if sec_cnt == 1 else "分歧回封烂板",
            "tags": tags,
            "risks": [r["tag"] for r in risks],
            "_sort_key": (priority, -lt, -zbc),
        })

    # 补充 #035 炸板未封但具反包潜力的活跃股（若 kline_map 提供当日上影炸板股）
    zt_codes = {_bare(s.get("code")) for s in stocks}
    if kline_map:
        recent_zt_codes = set()
        if ladder_hist is not None and not ladder_hist.empty:
            recent_zt_codes = set(ladder_hist.tail(400)["code"].astype(str).map(_bare))
        for bare, kdf in kline_map.items():
            b = _bare(bare)
            if is_bj_stock(b) or b in zt_codes or kdf is None or len(kdf) < 2:
                continue
            if recent_zt_codes and b not in recent_zt_codes:
                continue
            last = kdf.iloc[-1]
            prev = kdf.iloc[-2]
            prev_c = float(prev.get("close") or 0)
            hi = float(last.get("high") or 0)
            cl = float(last.get("close") or 0)
            if prev_c <= 0 or hi <= 0:
                continue
            limit_thresh = prev_c * (1.195 if b.startswith(("30", "68")) else 1.095)
            # 当日曾触及涨停但收盘炸板回落，且收盘仍红盘（涨幅 >= 2%）
            if hi >= limit_thresh and cl < limit_thresh * 0.992 and cl >= prev_c * 1.02:
                info = snap_map.get(b, {})
                pct_today = (cl - prev_c) / prev_c * 100.0
                items.append({
                    "code": b,
                    "name": info.get("name") or b,
                    "industry": info.get("industry") or "—",
                    "limit_times": 0,
                    "first_time": "盘中触板炸板",
                    "zbc": 1,
                    "sector_zt_count": ind_counts.get(info.get("industry") or "—", 0),
                    "close": cl,
                    "circ_mv_yi": info.get("circ_mv_yi"),
                    "seal_yi": None,
                    "turnover_rate": turnover_map.get(b),
                    "pattern_type": f"#035 触板炸板(收{pct_today:+.1f}%)",
                    "tags": [f"#035 冲板回落({pct_today:+.1f}%)", "观察次日竞价反包"],
                    "risks": [],
                    "_sort_key": (2, 0, -pct_today),
                })

    items.sort(key=lambda x: x["_sort_key"])
    for it in items:
        it.pop("_sort_key", None)

    return {
        "pool_key": "pool_ice_rebound",
        "title": "🦴 冰点「狗啃板」（烂而不弱）与炸板反包池（#080 / #118 / #035）",
        "rule_ids": ["080", "118", "035"],
        "methodology_note": (
            "依据 #080 与 #118：在分歧或冰点期，盘中反复炸板（≥2次）检验抛压但尾盘仍顽强回封、"
            "尤其是所属板块内唯一涨停的「狗啃板」，代表主力承接了全部恐慌筹码，次日极易弱转强领涨。"
            "叠加 #035：近期人气股盘中触板被动炸板但收红盘者，次日若竞价不核按钮则具备反包潜质。"
        ),
        "next_day_anchor": "次日 09:25 集合竞价若出现平开或高开（未闷杀低开<-3%），属于标准「弱转强」信号；若大幅低开核按钮则直接放弃。",
        "count": len(items),
        "items": items[:20],
    }


def _extract_kline_from_prices(
    prices_df: pd.DataFrame | None,
    codes: set[str],
    trade_date: str,
) -> dict[str, pd.DataFrame]:
    """当跑批未传入完整 OHLC K线时，从 scorecard/prices.csv 重建收盘价序列以计算真实 MA6 / MA12。"""
    if prices_df is None or prices_df.empty or not codes:
        return {}
    dk = _norm_date(trade_date)
    df = prices_df.copy()
    df["date"] = df["date"].astype(str).map(_norm_date)
    df = df[df["date"] <= dk]
    df["bare"] = df["ts_code"].astype(str).map(_bare)
    df = df[df["bare"].isin(codes)].sort_values(["bare", "date"])
    out: dict[str, pd.DataFrame] = {}
    for bare, grp in df.groupby("bare"):
        if len(grp) >= 6:
            sub = pd.DataFrame({
                "date": grp["date"].values,
                "close": pd.to_numeric(grp["adj_close"], errors="coerce").astype("float64").values,
            }).dropna(subset=["close"])
            if len(sub) >= 6:
                out[str(bare)] = sub
    return out


def build_pool_ma6_pullback(
    trade_date: str,
    ladder: dict | None,
    ladder_hist: pd.DataFrame | None,
    snap_map: dict[str, dict],
    turnover_map: dict[str, float],
    kline_map: dict[str, pd.DataFrame] | None = None,
    prices_df: pd.DataFrame | None = None,
) -> dict:
    """战法池 4：#128 人气龙头首阴/分歧回踩 MA6 / MA12 观察池。"""
    dk = _norm_date(trade_date)
    zt_today = {_bare(s.get("code")) for s in ((ladder or {}).get("stocks") or [])}

    # 1. 从近 12 个交易日涨停归档中找出曾达 ≥2 连板（优先 ≥3 板）的断板人气龙头
    leader_meta: dict[str, dict] = {}
    if ladder_hist is not None and not ladder_hist.empty:
        h = ladder_hist.copy()
        h["date"] = h["date"].astype(str).map(_norm_date)
        h = h[h["date"] <= dk]
        recent_dates = sorted(h["date"].unique())[-12:]
        h_rec = h[h["date"].isin(recent_dates)]
        for bare, grp in h_rec.groupby(h_rec["code"].astype(str).map(_bare)):
            if not bare or is_bj_stock(bare) or bare in zt_today:
                continue
            max_b = int(grp["limit_times"].max())
            if max_b < 2:
                continue
            last_row = grp.sort_values("date").iloc[-1]
            leader_meta[bare] = {
                "code": bare,
                "name": str(last_row.get("name") or bare),
                "industry": str(last_row.get("industry") or "—"),
                "max_boards": max_b,
                "last_zt_date": str(last_row.get("date") or ""),
            }

    k_lookup = dict(kline_map or {})
    missing_codes = set(leader_meta.keys()) - set(k_lookup.keys())
    if missing_codes and prices_df is not None:
        k_lookup.update(_extract_kline_from_prices(prices_df, missing_codes, dk))

    items = []
    for bare, meta in leader_meta.items():
        kdf = k_lookup.get(bare)
        if kdf is None or len(kdf) < 6:
            continue
        closes = pd.to_numeric(kdf["close"], errors="coerce").astype("float64").dropna()
        if len(closes) < 6:
            continue
        cur_close = float(closes.iloc[-1])
        if cur_close <= 0:
            continue
        ma6 = float(closes.tail(6).mean())
        ma12 = float(closes.tail(12).mean()) if len(closes) >= 12 else None

        dist_ma6 = (cur_close - ma6) / ma6 * 100.0 if ma6 > 0 else None
        dist_ma12 = (cur_close - ma12) / ma12 * 100.0 if (ma12 and ma12 > 0) else None

        # 判定是否处于 MA6 或 MA12 回踩支撑观察区（距离在 -3.0% ~ +4.5% 之间）
        hit_ma6 = dist_ma6 is not None and -2.5 <= dist_ma6 <= 4.0
        hit_ma12 = dist_ma12 is not None and -3.0 <= dist_ma12 <= 4.0
        if not (hit_ma6 or hit_ma12):
            continue

        pct_today = None
        if len(closes) >= 2 and closes.iloc[-2] > 0:
            pct_today = float((cur_close - closes.iloc[-2]) / closes.iloc[-2] * 100.0)
        # 剔除当日继续大涨 >6%（非回踩）或跌停破位的个股
        if pct_today is not None and (pct_today > 6.0 or pct_today <= -9.6):
            continue

        info = snap_map.get(bare, {})
        tr = turnover_map.get(bare)
        anchor_label = []
        if hit_ma6:
            anchor_label.append(f"贴近MA6({dist_ma6:+.1f}%)")
        if hit_ma12:
            anchor_label.append(f"贴近MA12({dist_ma12:+.1f}%)")

        tags = [f"#128 最高{meta['max_boards']}板龙头"] + anchor_label
        risks = check_stock_risk_gates(
            bare, meta["name"], tr, None, meta["max_boards"], None, kdf, ladder_hist
        )

        items.append({
            "code": bare,
            "name": info.get("name") or meta["name"],
            "industry": info.get("industry") or meta["industry"],
            "max_boards": meta["max_boards"],
            "last_zt_date": _fmt_date(meta["last_zt_date"]),
            "close": round(cur_close, 2),
            "pct_chg": round(pct_today, 2) if pct_today is not None else None,
            "ma6": round(ma6, 2),
            "ma12": round(ma12, 2) if ma12 is not None else None,
            "dist_ma6_pct": round(dist_ma6, 2) if dist_ma6 is not None else None,
            "dist_ma12_pct": round(dist_ma12, 2) if dist_ma12 is not None else None,
            "circ_mv_yi": info.get("circ_mv_yi"),
            "turnover_rate": tr,
            "support_anchor": " / ".join(anchor_label),
            "tags": tags,
            "risks": [r["tag"] for r in risks],
            "_sort_key": (-meta["max_boards"], abs(dist_ma6 or 99.0)),
        })

    items.sort(key=lambda x: x["_sort_key"])
    for it in items:
        it.pop("_sort_key", None)

    return {
        "pool_key": "pool_ma6_pullback",
        "title": "📐 人气龙头首阴回踩 MA6 / MA12 观察池（#128 强势股低吸精髓）",
        "rule_ids": ["128", "087", "107"],
        "methodology_note": (
            "依据 #128：作者实盘将常规 5日/10日均线改良为 **6日均线（MA6）与 12日均线（MA12）**，"
            "专门防范主力故意击穿 MA5/MA10 制造破位假象的洗盘骗线。近期连板人气龙头断板后首次缩量回踩 MA6（强势洗盘）或 MA12（波段防守线），是高盈亏比观察位。"
        ),
        "next_day_anchor": "次日盘中观察回踩 MA6 或 MA12 时的分时承接（急跌缩量不破均线、分时双底企稳），若放量有效跌破 MA12 且无反抽则放弃。",
        "count": len(items),
        "items": items[:20],
    }


def detect_rubbing_line(kdf: pd.DataFrame) -> dict | None:
    """检测最后两根 K 线是否构成 #169 经典揉搓线（前日长上影试盘 + 今日长下影洗盘）。"""
    if kdf is None or len(kdf) < 2:
        return None
    req = {"open", "high", "low", "close"}
    if not (req <= set(kdf.columns)):
        return None
    prev = kdf.iloc[-2]
    curr = kdf.iloc[-1]
    try:
        p_o, p_h, p_l, p_c = float(prev["open"]), float(prev["high"]), float(prev["low"]), float(prev["close"])
        c_o, c_h, c_l, c_c = float(curr["open"]), float(curr["high"]), float(curr["low"]), float(curr["close"])
    except (TypeError, ValueError):
        return None

    p_range = p_h - p_l
    c_range = c_h - c_l
    if p_range <= 0 or c_range <= 0 or p_c <= 0 or c_c <= 0:
        return None
    # 振幅门槛：前日与今日振幅均需 >= 2.5%，避免死水织布机误判
    if (p_range / p_c) < 0.025 or (c_range / c_c) < 0.025:
        return None

    p_upper = p_h - max(p_o, p_c)
    p_lower = min(p_o, p_c) - p_l
    c_upper = c_h - max(c_o, c_c)
    c_lower = min(c_o, c_c) - c_l

    p_upper_ratio = p_upper / p_range
    p_lower_ratio = p_lower / p_range
    c_lower_ratio = c_lower / c_range
    c_upper_ratio = c_upper / c_range

    # #169 揉搓线标准：前日上影占比 >= 45% 且下影 <= 35%；今日下影占比 >= 45% 且上影 <= 35%
    if p_upper_ratio >= 0.45 and p_lower_ratio <= 0.35 and c_lower_ratio >= 0.45 and c_upper_ratio <= 0.35:
        return {
            "prev_upper_ratio": round(p_upper_ratio * 100, 1),
            "curr_lower_ratio": round(c_lower_ratio * 100, 1),
            "desc": f"前日长上影({p_upper_ratio*100:.0f}%)试盘 + 今日长下影({c_lower_ratio*100:.0f}%)探底回升",
        }
    return None


def build_pool_rubbing_and_breakout(
    trade_date: str,
    strong_df: pd.DataFrame | None,
    breakout_df: pd.DataFrame | None,
    snap_map: dict[str, dict],
    turnover_map: dict[str, float],
    kline_map: dict[str, pd.DataFrame] | None = None,
    prices_df: pd.DataFrame | None = None,
) -> dict:
    """战法池 5：#169 揉搓线洗盘 & #139/#172 阶段突破缩量回踩池。"""
    dk = _norm_date(trade_date)
    # 汇总趋势/突破候选池元信息
    cand_meta: dict[str, dict] = {}
    if strong_df is not None and not strong_df.empty:
        for _, r in strong_df.iterrows():
            b = _bare(r.get("ts_code"))
            if not b or is_bj_stock(r.get("ts_code")) or is_bj_stock(b):
                continue
            cand_meta[b] = {
                "code": b,
                "name": str(r.get("name") or b),
                "industry": str(r.get("细分行业") or "—"),
                "source": f"RPS50={r.get('RPS_50', '—')}",
                "rps_50": pd.to_numeric(r.get("RPS_50"), errors="coerce"),
            }
    if breakout_df is not None and not breakout_df.empty:
        for _, r in breakout_df.iterrows():
            b = _bare(r.get("ts_code"))
            if not b or is_bj_stock(r.get("ts_code")) or is_bj_stock(b):
                continue
            lvl = str(r.get("level") or "阶段新高")
            if b in cand_meta:
                cand_meta[b]["source"] += f" + {lvl}"
            else:
                cand_meta[b] = {
                    "code": b,
                    "name": str(r.get("name") or b),
                    "industry": str(r.get("industry") or "—"),
                    "source": lvl,
                    "rps_50": None,
                }

    k_lookup = dict(kline_map or {})
    missing = set(cand_meta.keys()) - set(k_lookup.keys())
    if missing and prices_df is not None:
        k_lookup.update(_extract_kline_from_prices(prices_df, missing, dk))

    # 同时允许 kline_map 中非北交所标的命中 #169 揉搓线
    all_scan_codes = sorted(
        c for c in (set(cand_meta.keys()) | set(k_lookup.keys()))
        if not is_bj_stock(c)
    )

    items = []
    for bare in all_scan_codes:
        kdf = k_lookup.get(bare)
        if kdf is None or len(kdf) < 6:
            continue
        closes = pd.to_numeric(kdf["close"], errors="coerce").astype("float64").dropna()
        if len(closes) < 6:
            continue
        cur_close = float(closes.iloc[-1])
        ma6 = float(closes.tail(6).mean())
        ma12 = float(closes.tail(12).mean()) if len(closes) >= 12 else ma6
        if cur_close <= 0 or ma12 <= 0:
            continue

        pct_today = float((cur_close - closes.iloc[-2]) / closes.iloc[-2] * 100.0) if len(closes) >= 2 and closes.iloc[-2] > 0 else None
        dist_ma6 = (cur_close - ma6) / ma6 * 100.0
        dist_ma12 = (cur_close - ma12) / ma12 * 100.0

        rub = detect_rubbing_line(kdf)
        is_pullback = (
            bare in cand_meta
            and -2.0 <= dist_ma6 <= 3.5
            and cur_close >= ma12 * 0.985
            and (pct_today is None or -4.5 <= pct_today <= 3.5)
        )

        if not rub and not is_pullback:
            continue

        info = snap_map.get(bare, {})
        meta = cand_meta.get(bare, {})
        tr = turnover_map.get(bare)

        tags = []
        pattern_label = ""
        priority = 1
        if rub:
            tags.append(f"🔥 #169 揉搓线({rub['desc']})")
            pattern_label = "#169 揉搓线洗盘"
            priority = 0
        if is_pullback:
            src_txt = meta.get("source") or "趋势股"
            tags.append(f"#172 突破回踩MA6({dist_ma6:+.1f}%)")
            tags.append(src_txt)
            if not pattern_label:
                pattern_label = f"#172 突破回踩（{src_txt}）"

        risks = check_stock_risk_gates(
            bare, info.get("name") or meta.get("name") or bare, tr, None, None, None, kdf, None
        )

        items.append({
            "code": bare,
            "name": info.get("name") or meta.get("name") or bare,
            "industry": info.get("industry") or meta.get("industry") or "—",
            "pattern": pattern_label,
            "close": round(cur_close, 2),
            "pct_chg": round(pct_today, 2) if pct_today is not None else None,
            "ma6": round(ma6, 2),
            "ma12": round(ma12, 2),
            "dist_ma6_pct": round(dist_ma6, 2),
            "dist_ma12_pct": round(dist_ma12, 2),
            "circ_mv_yi": info.get("circ_mv_yi"),
            "turnover_rate": tr,
            "tags": tags,
            "risks": [r["tag"] for r in risks],
            "_sort_key": (priority, abs(dist_ma6)),
        })

    items.sort(key=lambda x: x["_sort_key"])
    for it in items:
        it.pop("_sort_key", None)

    return {
        "pool_key": "pool_rubbing_breakout",
        "title": "🌀 尾盘揉搓线洗盘 & 突破缩量回踩池（#169 / #139 / #172）",
        "rule_ids": ["169", "139", "172", "087"],
        "methodology_note": (
            "依据 #169：前日长上影试盘测试抛压、今日长下影探底洗盘拉起的「揉搓线」组合，是主力洗盘结束的经典右侧信号。"
            "依据 #139/#172：创阶段新高或进入高 RPS 池的强势股，首次缩量回踩 MA6/MA12 企稳，盈亏比显著优于突破当日追高。"
        ),
        "next_day_anchor": "揉搓线标的次日重点看能否放量站稳前日上影线半分位；突破回踩标的看 MA6/MA12 缩量止跌转阳。",
        "count": len(items),
        "items": items[:20],
    }


def build_pool_trend_catchup(
    ladder: dict | None,
    strong_df: pd.DataFrame | None,
    breakout_df: pd.DataFrame | None,
    snap_map: dict[str, dict],
    turnover_map: dict[str, float],
    ladder_hist: pd.DataFrame | None = None,
) -> dict:
    """战法池 6：#077/#103 强趋势板块低位首板补涨池（机构趋势大票搭台 + 游资小票首板唱戏）。"""
    # 1. 统计各行业在 RPS 强势榜与阶段新高池中的趋势锚点股
    sector_anchors: dict[str, list[str]] = {}
    if breakout_df is not None and not breakout_df.empty:
        for _, r in breakout_df.iterrows():
            if is_bj_stock(r.get("ts_code") or r.get("code")):
                continue
            ind = str(r.get("industry") or "").strip()
            nm = str(r.get("name") or "").strip()
            lvl = str(r.get("level") or "").replace("🏆 ", "").replace("🔥 ", "").replace("⚡ ", "")
            if ind and nm:
                entry = f"{nm}({lvl})"
                lst = sector_anchors.setdefault(ind, [])
                if entry not in lst:
                    lst.append(entry)

    if strong_df is not None and not strong_df.empty:
        for _, r in strong_df.iterrows():
            if is_bj_stock(r.get("ts_code") or r.get("code")):
                continue
            ind = str(r.get("细分行业") or "").strip()
            nm = str(r.get("name") or "").strip()
            rps = pd.to_numeric(r.get("RPS_50"), errors="coerce")
            if ind and nm and pd.notna(rps) and float(rps) >= 92.0:
                entry = f"{nm}(RPS{float(rps):.0f})"
                lst = sector_anchors.setdefault(ind, [])
                if len(lst) < 6 and all(nm not in x for x in lst):
                    lst.append(entry)

    # 强趋势行业：至少有 2 只趋势/新高锚点股
    strong_sectors = {k: v for k, v in sector_anchors.items() if len(v) >= 2}

    stocks = [s for s in ((ladder or {}).get("stocks") or []) if not is_bj_stock(s.get("code"))]
    items = []
    for s in stocks:
        lt = int(s.get("limit_times") or 1)
        if lt != 1:
            continue
        bare = _bare(s.get("code"))
        info = snap_map.get(bare, {})
        ind_ladder = str(s.get("industry") or "").strip()
        ind_snap = str(info.get("industry") or "").strip()

        matched_sec = None
        anchors = []
        for cand_ind in (ind_snap, ind_ladder):
            if cand_ind in strong_sectors:
                matched_sec = cand_ind
                anchors = strong_sectors[cand_ind]
                break
        if not matched_sec:
            # 模糊前缀匹配（例如 东财涨停池 '软件开发' vs 申万/Tushare '软件服务'，或前2字相同）
            for sec_k, sec_v in strong_sectors.items():
                if (len(ind_ladder) >= 2 and len(sec_k) >= 2 and ind_ladder[:2] == sec_k[:2]):
                    matched_sec = f"{ind_ladder}/{sec_k}"
                    anchors = sec_v
                    break

        if not matched_sec:
            continue

        ft = str(s.get("first_time") or "").strip()
        zbc, seal_yi = parse_zbc_and_seal(s.get("reason"))
        close_p = info.get("close")
        mv_yi = info.get("circ_mv_yi")
        tr = turnover_map.get(bare)

        tags = [
            f"#103 强趋势行业({len(anchors)}只趋势锚)",
            "#125 10点前首板" if is_pre_10am(ft) else "日内首板补涨",
        ]
        if mv_yi is not None and mv_yi <= 60.0:
            tags.append("#030 小市值弹性")

        risks = check_stock_risk_gates(
            bare, s.get("name", ""), tr, ft, lt, zbc, None, ladder_hist
        )

        items.append({
            "code": bare,
            "name": str(s.get("name") or info.get("name") or bare),
            "industry": matched_sec,
            "first_time": ft or "—",
            "close": close_p,
            "circ_mv_yi": mv_yi,
            "seal_yi": seal_yi,
            "zbc": zbc,
            "turnover_rate": tr,
            "trend_anchor_count": len(anchors),
            "trend_anchors": "、".join(anchors[:3]),
            "tags": tags,
            "risks": [r["tag"] for r in risks],
            "_sort_key": (
                0 if is_pre_10am(ft) else 1,
                -len(anchors),
                mv_yi if mv_yi is not None else 9999.0,
            ),
        })

    items.sort(key=lambda x: x["_sort_key"])
    for it in items:
        it.pop("_sort_key", None)

    return {
        "pool_key": "pool_trend_catchup",
        "title": "🚀 强趋势板块低位首板补涨池（#077 趋势与连板共振 × #103 强趋势补涨）",
        "rule_ids": ["077", "103", "030"],
        "methodology_note": (
            "依据 #077 与 #103：当某个行业内多只机构趋势大票持续创阶段新高或霸榜高 RPS（机构负责打造板块容量与持续性），"
            "板块内新启动的小市值、低股价首板往往成为游资进攻的连板补涨先锋。"
        ),
        "next_day_anchor": "次日观察同板块趋势大票是否维持红盘强势（板块不倒），若板块延续强势则前排小市值首板具备高1进2晋级潜质。",
        "count": len(items),
        "strong_sector_count": len(strong_sectors),
        "items": items[:20],
    }


# ================= 6. 自选股战法共振与排雷诊断（VIP 专属视角） =================

def diagnose_watchlist(
    watchlist: list[str] | None,
    pools: dict[str, dict],
    snap_map: dict[str, dict],
    turnover_map: dict[str, float],
    kline_map: dict[str, pd.DataFrame] | None = None,
    prices_df: pd.DataFrame | None = None,
    ladder_hist: pd.DataFrame | None = None,
    trade_date: str = "",
) -> dict:
    """对用户自选股执行 6 大战法池共振匹配、MA6/MA12 均线定位与 4 道防雷质检。"""
    if not watchlist:
        return {"status": "empty", "count": 0, "items": [], "summary": "暂无自选股"}

    dk = _norm_date(trade_date)
    bare_list = []
    seen = set()
    for c in watchlist:
        b = _bare(c)
        if b and b not in seen:
            seen.add(b)
            bare_list.append(b)

    # 建立战法池命中索引
    pool_hits_map: dict[str, list[str]] = {}
    for p_key, p_obj in (pools or {}).items():
        p_title = str(p_obj.get("title") or p_key).split("（")[0]
        for it in p_obj.get("items") or []:
            c = _bare(it.get("code"))
            if c:
                pool_hits_map.setdefault(c, []).append(p_title)

    k_lookup = dict(kline_map or {})
    missing = set(bare_list) - set(k_lookup.keys())
    if missing and prices_df is not None and dk:
        k_lookup.update(_extract_kline_from_prices(prices_df, missing, dk))

    diag_items = []
    resonance_cnt = 0
    risk_cnt = 0

    for b in bare_list:
        info = snap_map.get(b, {})
        name = info.get("name") or b
        ind = info.get("industry") or "—"
        close_p = info.get("close")
        tr = turnover_map.get(b)
        kdf = k_lookup.get(b)

        ma6, ma12, dist_ma6, dist_ma12, pct_today = None, None, None, None, None
        ma_status = "—"
        if kdf is not None and len(kdf) >= 6:
            closes = pd.to_numeric(kdf["close"], errors="coerce").astype("float64").dropna()
            if len(closes) >= 6:
                cur_c = float(closes.iloc[-1])
                if close_p is None:
                    close_p = round(cur_c, 2)
                ma6 = round(float(closes.tail(6).mean()), 2)
                if len(closes) >= 12:
                    ma12 = round(float(closes.tail(12).mean()), 2)
                if len(closes) >= 2 and closes.iloc[-2] > 0:
                    pct_today = round(float((cur_c - closes.iloc[-2]) / closes.iloc[-2] * 100.0), 2)
                if ma6 and ma6 > 0:
                    dist_ma6 = round((cur_c - ma6) / ma6 * 100.0, 2)
                if ma12 and ma12 > 0:
                    dist_ma12 = round((cur_c - ma12) / ma12 * 100.0, 2)

                if dist_ma6 is not None and -2.0 <= dist_ma6 <= 3.0:
                    ma_status = f"回踩MA6支撑区({dist_ma6:+.1f}%)"
                elif dist_ma12 is not None and -2.5 <= dist_ma12 <= 3.0:
                    ma_status = f"回踩MA12防守线({dist_ma12:+.1f}%)"
                elif dist_ma6 is not None and dist_ma6 > 3.0:
                    ma_status = f"站稳MA6上方({dist_ma6:+.1f}%)"
                elif dist_ma12 is not None and dist_ma12 < -2.5:
                    ma_status = f"跌破MA12下方({dist_ma12:+.1f}% · #025下降通道警戒)"

        hits = list(pool_hits_map.get(b, []))
        if kdf is not None:
            rub = detect_rubbing_line(kdf)
            if rub and not any("揉搓线" in h for h in hits):
                hits.append(f"🌀 #169 揉搓线({rub['desc']})")

        risks = check_stock_risk_gates(
            b, name, tr, None, None, None, kdf, ladder_hist
        )
        risk_tags = [r["tag"] for r in risks]

        if hits:
            resonance_cnt += 1
        if risk_tags:
            risk_cnt += 1

        diag_items.append({
            "code": b,
            "name": name,
            "industry": ind,
            "close": close_p,
            "pct_chg": pct_today,
            "ma6": ma6,
            "ma12": ma12,
            "dist_ma6_pct": dist_ma6,
            "dist_ma12_pct": dist_ma12,
            "ma_status": ma_status,
            "turnover_rate": tr,
            "pool_hits": hits,
            "risks": risk_tags,
        })

    return {
        "status": "ok",
        "count": len(diag_items),
        "resonance_count": resonance_cnt,
        "risk_count": risk_cnt,
        "summary": (
            f"自选股共 {len(diag_items)} 只：命中晚间战法池共振 {resonance_cnt} 只，"
            f"触发防雷质检标签 {risk_cnt} 只。"
        ),
        "items": diag_items,
    }


# ================= 7. 战法历史表现回看（VIP 战法成绩单） =================

def compute_pool_scorecard(
    ladder_hist: pd.DataFrame | None,
    prices_df: pd.DataFrame | None = None,
    before_date: str | None = None,
) -> dict:
    """统计各战法在真实历史归档中的次日晋级率与表现（如实披露样本量与周期切片）。"""
    if ladder_hist is None or ladder_hist.empty:
        return {"status": "failed", "reason": "涨停历史归档为空"}

    import sentiment as sm
    h = ladder_hist.copy()
    h = h[~h["code"].astype(str).map(is_bj_stock)]
    h["date"] = h["date"].astype(str).map(_norm_date)
    if before_date:
        h = h[h["date"] <= _norm_date(before_date)]

    dates = sorted(h["date"].unique())
    if len(dates) < 3:
        return {"status": "insufficient", "reason": f"归档仅 {len(dates)} 天，样本不足"}

    by_date = {d: grp for d, grp in h.groupby("date")}
    th = sm.phase_thresholds(h, before_date=dates[-1])

    # 统计每日：
    # 1. #125 10:00前首板 1进2 晋级率 vs 10:00后首板 1进2 晋级率
    # 2. #033 换手二板（非 09:25 一字）2进3 晋级率 vs 09:25 一字二板 2进3 晋级率
    # 3. 按当日情绪周期（发酵期/分歧期/退潮期/冰点期）分组统计次日晋级表现
    pre10_base, pre10_prom = 0, 0
    post10_base, post10_prom = 0, 0
    turn2_base, turn2_prom = 0, 0
    one2_base, one2_prom = 0, 0

    phase_buckets: dict[str, dict] = {
        "发酵期": {"days": 0, "pre10_base": 0, "pre10_prom": 0, "turn2_base": 0, "turn2_prom": 0},
        "分歧期": {"days": 0, "pre10_base": 0, "pre10_prom": 0, "turn2_base": 0, "turn2_prom": 0},
        "退潮期": {"days": 0, "pre10_base": 0, "pre10_prom": 0, "turn2_base": 0, "turn2_prom": 0},
        "冰点期": {"days": 0, "pre10_base": 0, "pre10_prom": 0, "turn2_base": 0, "turn2_prom": 0},
    }

    for i in range(len(dates) - 1):
        d_cur = dates[i]
        d_next = dates[i + 1]
        df_cur = by_date[d_cur]
        df_next = by_date[d_next]
        next_map = dict(zip(df_next["code"].astype(str).map(_bare), df_next["limit_times"]))

        # 当日首板
        fb = df_cur[df_cur["limit_times"] == 1]
        if len(fb) < 5:
            continue
        fb_prom_all = sum(1 for c in fb["code"].astype(str).map(_bare) if next_map.get(c, 0) >= 2)
        r12_cur = fb_prom_all / len(fb)
        ph_name = sm.classify_phase(r12_cur, th)

        mask_pre = fb["first_time"].map(is_pre_10am)
        fb_pre = fb[mask_pre]
        fb_post = fb[~mask_pre]

        p_pre = sum(1 for c in fb_pre["code"].astype(str).map(_bare) if next_map.get(c, 0) >= 2)
        p_post = sum(1 for c in fb_post["code"].astype(str).map(_bare) if next_map.get(c, 0) >= 2)

        pre10_base += len(fb_pre)
        pre10_prom += p_pre
        post10_base += len(fb_post)
        post10_prom += p_post

        # 当日二板
        tb = df_cur[df_cur["limit_times"] == 2]
        mask_one = tb["first_time"].astype(str).str.startswith("09:25")
        tb_turn = tb[~mask_one]
        tb_one = tb[mask_one]

        p_turn2 = sum(1 for c in tb_turn["code"].astype(str).map(_bare) if next_map.get(c, 0) >= 3)
        p_one2 = sum(1 for c in tb_one["code"].astype(str).map(_bare) if next_map.get(c, 0) >= 3)

        turn2_base += len(tb_turn)
        turn2_prom += p_turn2
        one2_base += len(tb_one)
        one2_prom += p_one2

        if ph_name in phase_buckets:
            b_obj = phase_buckets[ph_name]
            b_obj["days"] += 1
            b_obj["pre10_base"] += len(fb_pre)
            b_obj["pre10_prom"] += p_pre
            b_obj["turn2_base"] += len(tb_turn)
            b_obj["turn2_prom"] += p_turn2

    pre10_rate = float(pre10_prom / pre10_base) if pre10_base > 0 else None
    post10_rate = float(post10_prom / post10_base) if post10_base > 0 else None
    turn2_rate = float(turn2_prom / turn2_base) if turn2_base > 0 else None
    one2_rate = float(one2_prom / one2_base) if one2_base > 0 else None

    phase_summary = []
    for ph_k in ("发酵期", "分歧期", "退潮期", "冰点期"):
        b = phase_buckets[ph_k]
        r_pre = float(b["pre10_prom"] / b["pre10_base"]) if b["pre10_base"] > 0 else None
        r_t2 = float(b["turn2_prom"] / b["turn2_base"]) if b["turn2_base"] > 0 else None
        phase_summary.append({
            "phase": ph_k,
            "trade_days": b["days"],
            "pre10_1to2_rate": r_pre,
            "pre10_sample": f"{b['pre10_prom']}/{b['pre10_base']}",
            "turnover_2to3_rate": r_t2,
            "turnover_2to3_sample": f"{b['turn2_prom']}/{b['turn2_base']}",
        })

    return {
        "status": "ok",
        "date_from": dates[0],
        "date_to": dates[-1],
        "trade_days": len(dates),
        "strategies": {
            "pre_10am_first": {
                "label": "#125 10:00前硬核首板（1进2）",
                "base": pre10_base,
                "promoted": pre10_prom,
                "promo_rate": pre10_rate,
                "benchmark_label": "10:00后普通首板（1进2）",
                "benchmark_base": post10_base,
                "benchmark_promoted": post10_prom,
                "benchmark_rate": post10_rate,
                "edge_pct": (
                    round((pre10_rate - post10_rate) * 100, 2)
                    if (pre10_rate is not None and post10_rate is not None) else None
                ),
            },
            "turnover_2b": {
                "label": "#033/#071 换手二板（2进3）",
                "base": turn2_base,
                "promoted": turn2_prom,
                "promo_rate": turn2_rate,
                "benchmark_label": "09:25 一字二板（2进3）",
                "benchmark_base": one2_base,
                "benchmark_promoted": one2_prom,
                "benchmark_rate": one2_rate,
                "edge_pct": (
                    round((turn2_rate - one2_rate) * 100, 2)
                    if (turn2_rate is not None and one2_rate is not None) else None
                ),
            },
        },
        "by_phase": phase_summary,
        "note": "基于站内真实涨停历史归档逐日回验（不构成未来收益承诺）。",
    }


# ================= 8. 次日盘中 5 大时间节点观察沙盘 =================

def build_intraday_timeline(
    pools: dict[str, dict],
    cycle_laws: dict,
) -> list[dict]:
    """生成次日盘中 5 个关键时间锚点的观察清单（将今日入选标的直接映射进时间轴）。"""
    p_10am = [it["name"] for it in (pools.get("pool_10am_first", {}).get("items") or [])[:4]]
    p_2b = [it["name"] for it in (pools.get("pool_2b_pk", {}).get("items") or [])[:4]]
    p_ice = [it["name"] for it in (pools.get("pool_ice_rebound", {}).get("items") or [])[:4]]
    p_ma6 = [it["name"] for it in (pools.get("pool_ma6_pullback", {}).get("items") or [])[:4]]
    p_rub = [it["name"] for it in (pools.get("pool_rubbing_breakout", {}).get("items") or [])[:4]]

    pk_groups = pools.get("pool_2b_pk", {}).get("pk_groups") or []
    pk_desc = "；".join(
        f"{g['height']}板同台({','.join(s['name'] for s in g['stocks'][:4])})"
        for g in pk_groups if g.get("count", 0) >= 2
    ) or "今日无≥3板多龙同台组"

    return [
        {
            "time_window": "09:15 - 09:25",
            "node_name": "集合竞价定方向（#049 中庸选股 × #080 弱转强检验）",
            "core_task": (
                "① 09:20 前挂单可撤，重点看 09:20 后真实封单与前排连板跌停是否减少；"
                "② 对同身位连板组执行 #049「去最高、去最低，看中间温和高开(+3%~+7%)」；"
                "③ 检验昨日烂板/狗啃板今日是否高开实现「弱转强」。"
            ),
            "watch_targets": (
                f"多龙PK：{pk_desc} ｜ 弱转强观察：{'、'.join(p_ice) if p_ice else '今日无狗啃板'}"
            ),
            "red_line": "若高标批量一字跌停（核按钮未消退）或目标股大幅低开<-4%，放弃早盘接力计划。",
        },
        {
            "time_window": "09:30 - 10:00",
            "node_name": "开盘黄金半小时（#125 十点前涨停 × #033 换手晋级）",
            "core_task": (
                "① 全天最核心的连板晋级与新题材首板集中在此时段诞生；"
                "② 观察昨日 10:00 前硬核首板与换手二板的分歧换手承接（分时均线之上缩量承接后放量拉升）。"
            ),
            "watch_targets": (
                f"1进2锚点：{'、'.join(p_10am) if p_10am else '—'} ｜ "
                f"连板锚点：{'、'.join(p_2b) if p_2b else '—'}"
            ),
            "red_line": "高潮次日早盘直线缩量秒板且无板块梯队配合者，谨防冲高炸板大面（#069）。",
        },
        {
            "time_window": "10:00 - 11:30",
            "node_name": "早盘分歧检验与均线支撑观察（#128 MA6/MA12 低吸）",
            "core_task": (
                "① 10:00 后连板跟风溢价显著下降，转向观察前期人气龙头回踩 MA6 / MA12 均线时的缩量企稳信号；"
                "② 观察早盘涨停股封单是否稳定、炸板率是否飙升。"
            ),
            "watch_targets": f"MA6/MA12观察：{'、'.join(p_ma6) if p_ma6 else '—'}",
            "red_line": "下跌通道标的严禁左侧潜伏（#025）；放量跌破 MA12 且反抽无力者剔除。",
        },
        {
            "time_window": "13:00 - 14:30",
            "node_name": "午后垃圾时间防偷袭（#032 不能打的板）",
            "core_task": (
                "① 午后无新逻辑催化的独苗直线偷袭板，次日溢价极低，坚持「宁可错过不打后排偷袭板」；"
                "② 仅当指数共振大涨且新题材全线井喷时，观察前排容量核心。"
            ),
            "watch_targets": "以持仓观察与风控为主，不新开后排跟风。",
            "red_line": "严禁在午后大盘回落期去接频繁开板（炸板≥3次）的后排跟风烂板（#032）。",
        },
        {
            "time_window": "14:30 - 15:00",
            "node_name": "尾盘节点确认（#169 揉搓线 × #035 被动炸板观察）",
            "core_task": (
                "① 14:45 后日内大盘风险基本释放，确认当日走出「长下影探底回升」构成 #169 揉搓线或 #172 突破回踩企稳的标的；"
                "② 记录尾盘因大盘跳水而被动炸板的核心人气股（#035），纳入当晚反包复盘池。"
            ),
            "watch_targets": f"揉搓线/突破回踩：{'、'.join(p_rub) if p_rub else '—'}",
            "red_line": "尾盘最后10分钟无故竞价抢筹拉升但全天成交稀疏的庄股形态（#154）坚决回避。",
        },
    ]


# ================= 9. 顶层组装与 Markdown 摘要输出 =================

def build_evening_plan_payload(
    trade_date: str,
    ladder: dict | None,
    derived: dict | None,
    ladder_hist: pd.DataFrame | None = None,
    breadth_hist: pd.DataFrame | None = None,
    snapshot_df: pd.DataFrame | None = None,
    strong_df: pd.DataFrame | None = None,
    breakout_df: pd.DataFrame | None = None,
    prices_df: pd.DataFrame | None = None,
    kline_map: dict[str, pd.DataFrame] | None = None,
    pct_map: dict | None = None,
) -> dict:
    """组装完整晚间复盘与次日交易计划产物（写入 data/evening_plan/latest.json）。"""
    dk = _norm_date(trade_date)
    if not dk or not ladder or not ladder.get("stocks"):
        return {
            "status": "failed",
            "date": dk or "",
            "reason": "缺少当日涨停池数据，无法生成晚间战法池",
            "disclaimer": DISCLAIMER,
        }

    snap_map = _build_snapshot_lookup(snapshot_df)
    turnover_map = _build_turnover_lookup(strong_df, breakout_df, kline_map)

    cycle_laws = detect_cycle_and_calendar_laws(
        dk, derived, ladder_hist, breadth_hist, pct_map
    )

    weekday_idx = None
    try:
        weekday_idx = datetime.datetime.strptime(dk, "%Y%m%d").weekday()
    except ValueError:
        pass

    pools = {
        "pool_10am_first": build_pool_10am_first(ladder, snap_map, turnover_map, ladder_hist),
        "pool_2b_pk": build_pool_2b_and_pk(ladder, snap_map, turnover_map, weekday_idx, ladder_hist),
        "pool_ice_rebound": build_pool_ice_rebound(ladder, snap_map, turnover_map, kline_map, ladder_hist),
        "pool_ma6_pullback": build_pool_ma6_pullback(
            dk, ladder, ladder_hist, snap_map, turnover_map, kline_map, prices_df
        ),
        "pool_rubbing_breakout": build_pool_rubbing_and_breakout(
            dk, strong_df, breakout_df, snap_map, turnover_map, kline_map, prices_df
        ),
        "pool_trend_catchup": build_pool_trend_catchup(
            ladder, strong_df, breakout_df, snap_map, turnover_map, ladder_hist
        ),
    }

    scorecard = compute_pool_scorecard(ladder_hist, prices_df, before_date=dk)
    timeline = build_intraday_timeline(pools, cycle_laws)

    total_candidates = sum(int(p.get("count") or 0) for p in pools.values())
    status = "complete" if (derived and derived.get("status") in ("complete", "ok")) else "incomplete"

    return {
        "status": status,
        "date": dk,
        "date_display": _fmt_date(dk),
        "generated_at": (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
        "total_candidates": total_candidates,
        "cycle_and_laws": cycle_laws,
        "pools": pools,
        "pool_scorecard": scorecard,
        "intraday_timeline": timeline,
        "disclaimer": DISCLAIMER,
    }


def evening_plan_markdown(
    payload: dict | None,
    watchlist: list[str] | None = None,
) -> tuple[str, str | None]:
    """为 digest.py 生成晚间战法精选与自选股战法共振推送段落。

    返回 (markdown_text, missing_reason)。
    """
    if not payload or payload.get("status") == "failed":
        reason = (payload or {}).get("reason") or "晚间战法池未生成"
        return "", reason

    dk_disp = payload.get("date_display") or _fmt_date(payload.get("date") or "")
    cycle = payload.get("cycle_and_laws") or {}
    laws = cycle.get("active_laws") or []
    pools = payload.get("pools") or {}

    lines = [f"### 🎯 晚间战法池与周期法则（{dk_disp} · {cycle.get('weekday', '')}）", ""]

    # 1. 触发的周期/日历特殊法则
    if laws:
        for lw in laws[:3]:
            icon = "🟢" if lw.get("level") == "opportunity" else ("⚠️" if lw.get("level") == "warning" else "📌")
            lines.append(f"- {icon} **{lw.get('title')}**：{lw.get('summary')}")
        lines.append("")

    # 2. 10:00前首板 vs 10:00后首板溢价对比（#125）
    sp125 = cycle.get("first_board_split_125") or {}
    if sp125.get("status") == "ok":
        lines.append(f"- ⏱️ **#125 分时封板溢价验证**：{sp125.get('verdict')}")
        lines.append("")

    # 3. 六大池代表性客观名单摘要
    pool_summaries = []
    for key, short_lbl in (
        ("pool_10am_first", "⚡ #125 10:00前硬核首板"),
        ("pool_2b_pk", "🐉 #033/#049 换手二板与多龙PK"),
        ("pool_ice_rebound", "🦴 #080/#118 狗啃板与分歧回封"),
        ("pool_ma6_pullback", "📐 #128 龙头回踩MA6/MA12"),
        ("pool_rubbing_breakout", "🌀 #169/#172 揉搓线与突破回踩"),
        ("pool_trend_catchup", "🚀 #103 强趋势板块首板补涨"),
    ):
        p = pools.get(key) or {}
        cnt = int(p.get("count") or 0)
        its = p.get("items") or []
        if cnt > 0 and its:
            names = "、".join(
                f"{it['name']}({it.get('support_anchor') or it.get('pattern_type') or it.get('first_time', '')})"
                for it in its[:4]
            )
            pool_summaries.append(f"- {short_lbl}（**{cnt}** 只）：{names}" + ("…" if cnt > 4 else ""))
        else:
            pool_summaries.append(f"- {short_lbl}：今日无触发标的")

    lines.extend(pool_summaries)

    # 4. VIP 个性化：自选股战法共振与排雷提示
    if watchlist:
        bare_wl = {_bare(c) for c in watchlist if _bare(c)}
        wl_hits = []
        for p in pools.values():
            p_title = str(p.get("title") or "").split("（")[0]
            for it in p.get("items") or []:
                c = _bare(it.get("code"))
                if c in bare_wl:
                    risk_str = f" ｜ {' '.join(it['risks'])}" if it.get("risks") else ""
                    wl_hits.append(f"{it['name']} → 命中【{p_title}】{risk_str}")
        if wl_hits:
            lines.append("")
            lines.append("**🔍 你的自选股战法池共振**：")
            for h_txt in wl_hits[:6]:
                lines.append(f"- {h_txt}")

    lines += ["", f"_{DISCLAIMER}_"]
    return "\n".join(lines), None


def save_evening_plan(payload: dict) -> str:
    os.makedirs(HIST_DIR, exist_ok=True)
    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    dk = _norm_date(payload.get("date") or "")
    if len(dk) == 8:
        with open(os.path.join(HIST_DIR, f"{dk}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    return LATEST_PATH


def load_evening_plan(date_key: str | None = None) -> dict | None:
    path = LATEST_PATH
    if date_key:
        dk = _norm_date(date_key)
        cand = os.path.join(HIST_DIR, f"{dk}.json")
        if os.path.exists(cand):
            path = cand
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_methodology_kb() -> dict:
    if not os.path.exists(METHODOLOGY_KB_PATH):
        return {"articles": [], "categories": [], "evening_steps": []}
    try:
        with open(METHODOLOGY_KB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"articles": [], "categories": [], "evening_steps": []}

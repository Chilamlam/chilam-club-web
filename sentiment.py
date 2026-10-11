# -*- coding: utf-8 -*-
"""
短线情绪派生指标计算层（纯 pandas/numpy，禁止 import streamlit / tushare / akshare）

为什么需要这些派生量：涨停家数、上涨家数这类原始计数只能说明「今天涨了多少」，
说明不了「明天还能不能接力」。真正有前瞻性的是接力成功率与溢价——
这也是 vibe-astock 把派生指标层单独抽出来、并且明确「不经过 AI」的原因：
它们是硬计算，AI 只负责把数串成一段话。

四个核心派生量：
  1. 晋级率（1进2 最敏感）：昨日 N 连板中，今日成功晋级 N+1 的比例
  2. 连板溢价：昨日连板股今日的平均/中位涨幅——衡量接力资金还愿不愿意出价
  3. 赚钱效应背离：全市场涨幅均值 vs 中位数。均值远高于中位数意味着
     指数被少数权重拉起、多数个股其实在亏钱，是典型的「指数虚涨」信号
  4. 梯队断层：某个连板高度上一只票都没有，说明高度接力链条断了

归档文件：
  data/sentiment/ladder_history.csv   date,code,name,industry,limit_times,first_time
  data/sentiment/breadth_history.csv  date,mean_pct,median_pct,up,down,limit_up,limit_down
  data/sentiment/derived.json         由 daily_sentiment.py 写出的派生指标产物
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

SENT_DIR = os.path.join("data", "sentiment")
LADDER_HIST = os.path.join(SENT_DIR, "ladder_history.csv")
BREADTH_HIST = os.path.join(SENT_DIR, "breadth_history.csv")
DERIVED_PATH = os.path.join(SENT_DIR, "derived.json")

LADDER_COLS = ["date", "code", "name", "industry", "limit_times", "first_time"]
BREADTH_COLS = ["date", "mean_pct", "median_pct", "up", "down", "limit_up", "limit_down"]


def _ensure_dir() -> None:
    os.makedirs(SENT_DIR, exist_ok=True)


def load_ladder_history() -> pd.DataFrame:
    if not os.path.exists(LADDER_HIST):
        return pd.DataFrame(columns=LADDER_COLS)
    df = pd.read_csv(LADDER_HIST, dtype={"code": str, "date": str})
    df["limit_times"] = pd.to_numeric(df["limit_times"], errors="coerce").fillna(1).astype(int)
    return df


def load_breadth_history() -> pd.DataFrame:
    if not os.path.exists(BREADTH_HIST):
        return pd.DataFrame(columns=BREADTH_COLS)
    df = pd.read_csv(BREADTH_HIST, dtype={"date": str})
    for c in ("mean_pct", "median_pct"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df


def append_ladder(rows: pd.DataFrame) -> int:
    _ensure_dir()
    if rows is None or rows.empty:
        return len(load_ladder_history())
    rows = rows.copy()
    for c in LADDER_COLS:
        if c not in rows.columns:
            rows[c] = np.nan
    merged = pd.concat([load_ladder_history(), rows[LADDER_COLS]], ignore_index=True)
    merged = merged.drop_duplicates(subset=["date", "code"], keep="last")
    merged = merged.sort_values(["date", "limit_times"], ascending=[True, False])
    merged.to_csv(LADDER_HIST, index=False, encoding="utf-8")
    return len(merged)


def append_breadth(row: dict) -> int:
    _ensure_dir()
    df = pd.concat([load_breadth_history(), pd.DataFrame([row])], ignore_index=True)
    df = df.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    df[BREADTH_COLS].to_csv(BREADTH_HIST, index=False, encoding="utf-8")
    return len(df)


# ================= 派生指标 =================

def promotion_rates(hist: pd.DataFrame, date: str, prev_date: str) -> dict:
    """
    晋级率：prev_date 的 N 连板中，date 成功晋级到 N+1 的比例。

    1进2 最敏感——首板数量通常几十只，样本足，且直接反映次日资金接力意愿；
    高位板（4 进 5 以上）本身只有几只，比率波动巨大，必须标注样本数由前端弱化展示。
    """
    if hist.empty:
        return {"status": "failed", "reason": "涨停归档为空"}
    prev = hist[hist["date"] == prev_date]
    cur = hist[hist["date"] == date]
    if prev.empty or cur.empty:
        return {"status": "failed",
                "reason": f"缺少 {prev_date} 或 {date} 的涨停归档，无法计算晋级率"}

    cur_map = dict(zip(cur["code"].astype(str), cur["limit_times"]))
    out = {}
    for n in sorted(prev["limit_times"].unique()):
        group = prev[prev["limit_times"] == n]
        base = len(group)
        if base == 0:
            continue
        promoted = 0
        for code in group["code"].astype(str):
            if cur_map.get(code, 0) >= n + 1:
                promoted += 1
        out[f"{int(n)}进{int(n) + 1}"] = {
            "base": int(base),
            "promoted": int(promoted),
            "rate": float(promoted / base),
            "reliable": bool(base >= 10),   # 样本 <10 的高位板比率不可当信号看
        }
    return {"status": "ok", "prev_date": prev_date, "date": date, "rates": out}


def ladder_gap(hist: pd.DataFrame, date: str) -> dict:
    """
    梯队断层：从最高板往下数，哪个高度一只票都没有。
    断层意味着高度接力链条断了——比「最高板是几板」更能说明情绪结构。

    断层的严格定义（2026-08-29 修正）：**被上下都有票的高度夹住的空档**。
    最低端没有票（例如今日 0 只首板）不算断层——那是「今天没有新增涨停」，
    是另一件事（另用 first_board 字段单独反映），把它混进 gaps 会让
    「链条断裂」这个信号失真。
    """
    cur = hist[hist["date"] == date]
    if cur.empty:
        return {"status": "failed", "reason": f"缺少 {date} 涨停归档"}
    counts = cur.groupby("limit_times").size().to_dict()
    heights = [int(h) for h in counts if int(counts[h]) > 0]
    max_h = max(heights)
    min_h = min(heights)

    tiers = []
    gaps = []
    for n in range(max_h, 0, -1):
        c = int(counts.get(n, 0))
        tiers.append({"height": n, "count": c})
        if c == 0 and min_h < n < max_h:
            gaps.append(n)

    first_board = int(counts.get(1, 0))
    verdict = (f"{max_h} 板成为孤高，中间 {gaps} 板断层，接力链条不连续"
               if gaps else
               f"{max_h} 板往下梯队连续，接力链条完整")
    if first_board == 0:
        verdict += "；今日无首板（无新增涨停接力）"

    return {
        "status": "ok",
        "max_height": max_h,
        "min_height": min_h,
        "first_board": first_board,
        "total": int(len(cur)),
        "tiers": tiers,
        "gaps": gaps,
        "verdict": verdict,
    }


def continuation_premium(hist: pd.DataFrame, prev_date: str,
                         pct_map: dict) -> dict:
    """
    连板溢价：prev_date 的连板股（≥2板）今日实际涨幅表现。
    pct_map: {纯代码: 今日涨幅%}，由跑批层传入（不在计算层联网）。

    中位数为主口径：均值容易被个别 20cm 涨停拉高，
    中位数才代表「昨天追高的人今天的平均处境」。
    """
    if hist.empty or not pct_map:
        return {"status": "failed", "reason": "缺少涨停归档或今日涨幅数据"}
    prev = hist[(hist["date"] == prev_date) & (hist["limit_times"] >= 2)]
    if prev.empty:
        return {"status": "failed", "reason": f"{prev_date} 无 2 板及以上标的"}

    vals, missing = [], 0
    for code in prev["code"].astype(str):
        v = pct_map.get(code)
        if v is None:
            missing += 1
            continue
        vals.append(float(v))
    if not vals:
        return {"status": "failed", "reason": "连板股今日涨幅全部缺失"}

    s = pd.Series(vals, dtype="float64")
    return {
        "status": "ok" if len(vals) >= 5 else "insufficient",
        "prev_date": prev_date,
        "n": int(len(vals)),
        "missing": int(missing),
        "median_pct": float(s.median()),
        "mean_pct": float(s.mean()),
        "win_rate": float((s > 0).mean()),
        "limit_again": int((s >= 9.8).sum()),
        "reason": (None if len(vals) >= 5 else f"样本仅 {len(vals)} 只，参考价值有限"),
    }


def profit_effect(mean_pct: float | None, median_pct: float | None,
                  up: int | None, down: int | None) -> dict:
    """
    赚钱效应与均值-中位数背离。

    均值显著高于中位数 = 少数权重/妖股把均值拉高，多数个股其实在跌，
    也就是「指数涨了但你亏钱」的那种日子。这个背离比涨跌家数更能解释体感。
    """
    if mean_pct is None or median_pct is None:
        return {"status": "failed", "reason": "缺少全市场涨幅均值/中位数"}
    gap = float(mean_pct) - float(median_pct)
    total = (up or 0) + (down or 0)
    verdict = "均值与中位数基本一致，涨跌较为普遍"
    if gap >= 0.5:
        verdict = "均值明显高于中位数：涨幅集中在少数标的，多数个股跑输体感偏冷"
    elif gap <= -0.5:
        verdict = "中位数高于均值：少数大跌股拖低均值，普涨结构其实尚可"
    return {
        "status": "ok",
        "mean_pct": float(mean_pct),
        "median_pct": float(median_pct),
        "divergence": gap,
        "up": int(up or 0),
        "down": int(down or 0),
        "up_ratio": (float(up / total) if total else None),
        "verdict": verdict,
    }


# ---- 周期阈值：按自身历史分位自适应，而不是写死绝对数字 ----

PHASE_LOOKBACK = 60      # 滚动窗口长度（交易日）
PHASE_MIN_SAMPLES = 20   # 分位法最低样本数；不足则回退经验阈值
PHASE_QUANTILES = (0.80, 0.55, 0.25)   # 分别对应 发酵 / 分歧 / 退潮 三档门槛
PHASE_FALLBACK = (0.21, 0.16, 0.11)    # 归档不足以估分位时的经验阈值

# 归档里首板少于该数目的日子不进分布（样本太小，比率全是噪声）
PHASE_MIN_BASE = 10


def promotion_rate_series(hist: pd.DataFrame,
                          before_date: str | None = None) -> pd.Series:
    """
    重建逐日 1进2 晋级率序列（index = 日期），用于给当日的 r12 定位。

    **只用严格早于 before_date 的交易日**——这是判定基准的方法论底线：
    阈值若包含当日自身的晋级率，等于用今天的结果解释今天的定位，既没有
    前瞻价值，也会让标签自指（当日 r12 必然落在自己的分位区间内）。
    首板样本 < PHASE_MIN_BASE 的日子一并剔除，噪声不进分布。
    """
    if hist is None or hist.empty:
        return pd.Series(dtype="float64")

    h = hist.copy()
    h["date"] = h["date"].astype(str)
    if before_date is not None:
        h = h[h["date"] < str(before_date)]
    if h.empty:
        return pd.Series(dtype="float64")

    by_date = {d: g for d, g in h.groupby("date")}
    dates = sorted(by_date)
    vals, idx = [], []
    for i in range(1, len(dates)):
        first_board = by_date[dates[i - 1]]
        first_board = first_board[first_board["limit_times"] == 1]
        base = len(first_board)
        if base < PHASE_MIN_BASE:
            continue
        cur = by_date[dates[i]]
        cur_map = dict(zip(cur["code"].astype(str), cur["limit_times"]))
        promoted = sum(1 for c in first_board["code"].astype(str)
                       if cur_map.get(c, 0) >= 2)
        vals.append(promoted / base)
        idx.append(dates[i])
    if not vals:
        return pd.Series(dtype="float64")
    return pd.Series(vals, index=idx, dtype="float64")


def phase_thresholds(hist: pd.DataFrame, before_date: str | None = None,
                     lookback: int = PHASE_LOOKBACK) -> dict:
    """
    四档阈值：取「自身历史」的分位，而不是拍一组绝对数字。

    为什么必须自适应：旧版硬编码 45/30/15，是用体感拍的、未经分布校准。
    用 2026-08-10~10-09 共 35 个交易日实测：1进2 晋级率均值 15.5%、中位 15.4%、
    最高 36.0%——45% 一次都没出现过，于是「发酵期」永不触发，97% 的日子被判
    退潮或冰点（判定基准与真实分布不同刻度）。分位阈值让四档都能出现，且随
    市场整体强弱自动漂移：市场转强，门槛跟着抬高；转弱则自动降低。

    分位塌陷（q80≈q55）时视为样本退化，回退经验阈值——宁可少信息，
    不要错档位。
    """
    series = promotion_rate_series(hist, before_date)
    n = int(len(series))

    if n >= PHASE_MIN_SAMPLES:
        window = series.iloc[-lookback:]
        q_high, q_mid, q_low = (float(window.quantile(q)) for q in PHASE_QUANTILES)
        if q_high > q_mid > q_low >= 0:
            return {
                "method": "percentile",
                "high": q_high, "mid": q_mid, "low": q_low,
                "samples": int(len(window)),
                "lookback": int(lookback),
                "window_from": str(window.index[0]),
                "window_to": str(window.index[-1]),
                "note": (f"自适应阈值：近 {len(window)} 个交易日 1进2 晋级率分位 "
                         f"p80/p55/p25 = {q_high * 100:.1f}% / {q_mid * 100:.1f}% / "
                         f"{q_low * 100:.1f}%"),
            }

    hi, mid, lo = PHASE_FALLBACK
    return {
        "method": "fallback",
        "high": hi, "mid": mid, "low": lo,
        "samples": n,
        "lookback": int(lookback),
        "window_from": None, "window_to": None,
        "note": (f"样本仅 {n} 个交易日（<{PHASE_MIN_SAMPLES}），暂用经验阈值 "
                 f"{hi * 100:.0f}% / {mid * 100:.0f}% / {lo * 100:.0f}%，"
                 f"样本积累后自动切换为分位阈值"),
    }


def _normalize_thresholds(thresholds: dict | None) -> dict:
    """
    校验外部传入的阈值：必须严格递减且非负，否则回退经验阈值。

    为什么宁可静默回退也不报错：阈值来自上一个环节的分位计算，一旦被传成
    NaN / 逆序，会让所有日子滑进同一个档位而不报错——这正是旧版 45/30/15
    最典型的事故形态（97% 判退潮/冰点却毫无异常提示）。
    """
    if isinstance(thresholds, dict):
        try:
            h = float(thresholds["high"])
            m = float(thresholds["mid"])
            l = float(thresholds["low"])
            if h > m > l >= 0:
                out = dict(thresholds)
                out.update({"high": h, "mid": m, "low": l})
                return out
        except (KeyError, TypeError, ValueError):
            pass
    hi, mid, lo = PHASE_FALLBACK
    return {"method": "fallback", "high": hi, "mid": mid, "low": lo,
            "samples": 0, "lookback": 0, "window_from": None, "window_to": None,
            "note": "未提供有效阈值，沿用经验阈值"}


def classify_phase(r12: float, thresholds: dict | None,
                   prem: float | None = None) -> str:
    """
    档位判定的**唯一真源**：周期定位与验证条件共用，避免同一规则两处实现漂移。

    只有两处生效条件：1进2 晋级率（主轴）与连板溢价（发酵期需要昨日连板
    仍有正溢价，否则「接力顺畅」与溢价为负互相矛盾）。
    """
    th = _normalize_thresholds(thresholds)
    if r12 >= th["high"] and (prem is None or prem > 0):
        return "发酵期"
    if r12 >= th["mid"]:
        return "分歧期"
    if r12 >= th["low"]:
        return "退潮期"
    return "冰点期"


def cycle_phase(promo: dict, gap: dict, premium: dict,
                thresholds: dict | None = None) -> dict:
    """
    情绪周期定位。只给「当前处于哪一段」的判断依据，不给参与建议、不给仓位。

    判据（全部来自上面的硬指标，不经过 AI）：
      1进2 晋级率是主轴——它决定次日打板资金愿不愿意继续接力
      连板溢价为负 = 昨日追高的人在亏钱，退潮特征
      梯队断层 = 高度链条断裂

    阈值来源（2026-10-11 修正）：不再写死绝对数字，由 phase_thresholds() 按
    自身历史分位给出（近 60 日 p80/p55/p25），样本不足时回退经验阈值。
    旧版硬编码 45/30/15 与真实分布严重不同刻度（详见 phase_thresholds 文档）。
    """
    r12 = None
    rates = (promo or {}).get("rates") or {}
    if "1进2" in rates and rates["1进2"].get("reliable"):
        r12 = float(rates["1进2"]["rate"])

    prem = (premium or {}).get("median_pct") if (premium or {}).get("status") == "ok" else None
    has_gap = bool((gap or {}).get("gaps"))

    if r12 is None:
        return {"status": "insufficient",
                "reason": "1进2 样本不足或缺失，无法定位周期（不做猜测）"}

    th = _normalize_thresholds(thresholds)
    phase = classify_phase(r12, th, prem)
    pct = r12 * 100
    hi, mid, lo = th["high"] * 100, th["mid"] * 100, th["low"] * 100

    if phase == "发酵期":
        desc = (f"接力顺畅，1进2 晋级率 {pct:.1f}% 站上高位阈值 {hi:.1f}%，"
                f"且昨日连板仍有正溢价")
    elif phase == "分歧期":
        desc = (f"晋级率 {pct:.1f}% 处于中位区间（{mid:.1f}%~{hi:.1f}%），"
                f"资金在分歧中切换，赚钱效应不均匀")
    elif phase == "退潮期":
        desc = (f"晋级率 {pct:.1f}% 已跌破中位阈值 {mid:.1f}%，"
                f"但仍在低位阈值 {lo:.1f}% 之上，接力开始失败")
    else:
        desc = (f"晋级率 {pct:.1f}% 跌破低位阈值 {lo:.1f}%，"
                f"打板链条基本断裂")

    if has_gap and phase in ("发酵期", "分歧期"):
        desc += "；但梯队存在断层，高度接力并不连续"

    return {
        "status": "ok",
        "phase": phase,
        "basis": desc,
        "r12": r12,
        "premium_median": prem,
        "thresholds": th,
        "note": "周期定位仅描述当前市场状态，不构成参与建议或仓位指引。",
    }


def verification_plan(promo: dict, gap: dict, premium: dict,
                      breadth: dict, thresholds: dict | None = None) -> list[dict]:
    """
    「明日验证条件」清单：把今晚的判断落成带**今日基准值 + 变动阈值**的可对账条目。

    为什么必须带基准值：不带基准的判断第二天无法核对，也就永远无法被证伪，
    这类「怎么说都对」的话术是信任的头号杀手。每条都写清楚
    「今天是多少 / 明天低于或高于多少算什么」，第二天可以逐条打勾。

    晋级率这一条的刻度直接复用 cycle_phase 的阈值（同一个 classify_phase），
    不再另算一套——同一规则两处实现必然漂移。
    """
    plan = []
    rates = (promo or {}).get("rates") or {}

    if "1进2" in rates and rates["1进2"].get("reliable"):
        cur = rates["1进2"]["rate"] * 100
        th = _normalize_thresholds(thresholds)
        prem = (premium or {}).get("median_pct") if (premium or {}).get("status") == "ok" else None
        now = classify_phase(cur / 100, th, prem)
        plan.append({
            "指标": "1进2 晋级率",
            "今日基准": f"{cur:.1f}%（{rates['1进2']['promoted']}/{rates['1进2']['base']}）"
                        f"，当前档位「{now}」",
            "验证条件": f"次日跌破 {th['low'] * 100:.1f}% → 冰点期；"
                        f"跌破 {th['mid'] * 100:.1f}% → 退潮期；"
                        f"站上 {th['high'] * 100:.1f}% → 发酵期",
            "为什么看它": "首板数量足、样本稳定，是次日资金接力意愿最敏感的前瞻指标",
        })

    if (premium or {}).get("status") == "ok":
        p = premium["median_pct"]
        plan.append({
            "指标": "昨日连板股今日中位涨幅",
            "今日基准": f"{p:+.2f}%（{premium['n']} 只，胜率 {premium['win_rate'] * 100:.0f}%）",
            "验证条件": "明日若转负且胜率跌破 40%，说明接力资金已停止出价（退潮确认）",
            "为什么看它": "衡量「昨天追高的人今天赚不赚钱」，比涨停家数更贴近真实体感",
        })

    if (gap or {}).get("status") == "ok":
        plan.append({
            "指标": "连板梯队最高高度",
            "今日基准": f"{gap['max_height']} 板"
                        + (f"，断层于 {gap['gaps']} 板" if gap.get("gaps") else "，梯队连续"),
            "验证条件": f"明日若最高板不足 {gap['max_height']} 板且无新晋高度，高度链条判定为断裂",
            "为什么看它": "高度是情绪的天花板，塌了通常先于家数走弱",
        })

    if (breadth or {}).get("status") == "ok":
        d = breadth["divergence"]
        plan.append({
            "指标": "全市场涨幅 均值−中位数 背离",
            "今日基准": f"{d:+.2f} 个百分点"
                        f"（均值 {breadth['mean_pct']:+.2f}% / 中位 {breadth['median_pct']:+.2f}%）",
            "验证条件": "明日若背离扩大至 +1.0 以上，说明赚钱效应进一步向少数标的集中",
            "为什么看它": "解释「指数涨了但我亏钱」的直接原因，涨跌家数看不出这一层",
        })

    return plan


# ================= 产物读写 =================

def save_derived(payload: dict) -> str:
    _ensure_dir()
    with open(DERIVED_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return DERIVED_PATH


def load_derived() -> dict | None:
    if not os.path.exists(DERIVED_PATH):
        return None
    try:
        with open(DERIVED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

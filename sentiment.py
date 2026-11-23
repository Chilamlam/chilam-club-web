# -*- coding: utf-8 -*-
"""
短线情绪派生指标计算层（纯 pandas/numpy，禁止 import streamlit / tushare / akshare）

所有指标都是收盘后的样本统计。统计档位不代表未来收益，文案约束见 REVIEW_CONTENT.md。

四个核心派生量：
  1. 晋级率（1进2 最敏感）：昨日 N 连板中，今日成功晋级 N+1 的比例
  2. 连板溢价：昨日连板股今日的平均/中位涨幅
  3. 全市场个股涨幅均值与中位数之差；不推断指数贡献或资金意图
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

    各高度列出完整归档群体的数量和逐股结果，不按数量评判参考价值。
    未出现在当日涨停归档的股票标为未晋级，不推断其跌幅或是否可成交。
    """
    if hist.empty:
        return {"status": "failed", "reason": "涨停归档为空"}
    prev = hist[hist["date"] == prev_date].drop_duplicates("code", keep="last")
    cur = hist[hist["date"] == date].drop_duplicates("code", keep="last")
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
        members = []
        for row in group.to_dict("records"):
            code = str(row["code"])
            height = cur_map.get(code)
            members.append({"code": code, "name": str(row.get("name", "")),
                            "previous_height": int(n),
                            "current_height": int(height) if height is not None else None,
                            "promoted": bool(height is not None and height >= n + 1)})
        promoted = sum(row["promoted"] for row in members)
        out[f"{int(n)}进{int(n) + 1}"] = {
            "base": int(base),
            "promoted": int(promoted),
            "rate": float(promoted / base),
            "not_promoted": int(base - promoted),
            "members": members,
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
    verdict = (f"最高 {max_h} 板，中间 {gaps} 板断层（该高度无股票）"
               if gaps else
               f"最高 {max_h} 板，中间高度无空档")
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

    同列中位数和均值，均为前收至今收的涨跌幅，不模拟个人买卖。
    """
    if hist.empty or not pct_map:
        return {"status": "failed", "reason": "缺少涨停归档或今日涨幅数据"}
    prev = hist[(hist["date"] == prev_date) & (hist["limit_times"] >= 2)]
    if prev.empty:
        return {"status": "failed", "reason": f"{prev_date} 无 2 板及以上标的"}

    vals, missing = [], 0
    for code in prev["code"].astype(str):
        v = pct_map.get(code)
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = float("nan")
        if not np.isfinite(v):
            missing += 1
            continue
        vals.append(float(v))
    if not vals:
        return {"status": "failed", "reason": "连板股今日涨幅全部缺失"}

    s = pd.Series(vals, dtype="float64")
    return {
        "status": "ok",
        "prev_date": prev_date,
        "n": int(len(vals)),
        "missing": int(missing),
        "median_pct": float(s.median()),
        "mean_pct": float(s.mean()),
        "win_rate": float((s > 0).mean()),
        "limit_again": int((s >= 9.8).sum()),
        "reason": None,
    }


def profit_effect(mean_pct: float | None, median_pct: float | None,
                  up: int | None, down: int | None) -> dict:
    """
    全市场个股涨跌幅均值、中位数及二者之差。

    这些汇总值不能单独识别权重贡献、收益集中度或投资者实际盈亏。
    """
    if mean_pct is None or median_pct is None:
        return {"status": "failed", "reason": "缺少全市场涨幅均值/中位数"}
    gap = float(mean_pct) - float(median_pct)
    total = (up or 0) + (down or 0)
    verdict = (f"个股涨跌幅均值 {float(mean_pct):+.2f}%，"
               f"中位数 {float(median_pct):+.2f}%，相差 {gap:+.2f} 个百分点")
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

# 只要求当日确有首板；数量本身属于市场状态的一部分。
PHASE_MIN_BASE = 1


def promotion_rate_series(hist: pd.DataFrame,
                          before_date: str | None = None) -> pd.Series:
    """
    重建逐日 1进2 晋级率序列（index = 日期），用于给当日的 r12 定位。

    **只用严格早于 before_date 的交易日**——这是判定基准的方法论底线：
    阈值若包含当日自身的晋级率，等于用今天的结果解释今天的定位，既没有
    前瞻价值，也会让标签自指（当日 r12 必然落在自己的分位区间内）。
    每个有首板归档的交易日均进入分布。
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
        "note": (f"当前用于滚动分位的窗口为 {n} 个交易日，尚未达到 "
                 f"{PHASE_MIN_SAMPLES} 日计算条件；现阶段使用经验阈值 "
                 f"{hi * 100:.0f}% / {mid * 100:.0f}% / {lo * 100:.0f}%，"
                 f"达到条件后自动切换为分位阈值"),
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
    连板统计档位的唯一判定函数。

    输入为1进2晋级率与昨日连板样本今日中位涨跌幅；档位不作为预测条件。
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
    按连板样本的晋级比例与中位涨跌幅划分统计档位。
    不从档位推断资金意愿或交易盈亏；中间缺档作为独立事实列出。

    阈值来源（2026-10-11 修正）：不再写死绝对数字，由 phase_thresholds() 按
    自身历史分位给出（近 60 日 p80/p55/p25），样本不足时回退经验阈值。
    旧版硬编码 45/30/15 与真实分布严重不同刻度（详见 phase_thresholds 文档）。
    """
    r12 = None
    rates = (promo or {}).get("rates") or {}
    if "1进2" in rates and rates["1进2"].get("base", 0) > 0:
        r12 = float(rates["1进2"]["rate"])

    prem = (premium or {}).get("median_pct") if (premium or {}).get("status") == "ok" else None
    has_gap = bool((gap or {}).get("gaps"))

    if r12 is None:
        return {"status": "insufficient",
                "reason": "缺少1进2记录，无法计算统计档位"}

    th = _normalize_thresholds(thresholds)
    phase = classify_phase(r12, th, prem)
    pct = r12 * 100
    desc = f"首板次日继续涨停比例 {pct:.1f}%"
    if prem is not None:
        desc += f"；昨日连板样本今日中位涨跌幅 {prem:+.2f}%"

    if has_gap and phase in ("发酵期", "分歧期"):
        desc += f"；中间 {gap['gaps']} 板无股票"

    return {
        "status": "ok",
        "phase": phase,
        "basis": desc,
        "r12": r12,
        "premium_median": prem,
        "thresholds": th,
        "note": "档位是连板样本的统计分组，不构成参与建议；未验证其预测收益的能力。",
    }


def verification_plan(promo: dict, gap: dict, premium: dict,
                      breadth: dict, thresholds: dict | None = None) -> list[dict]:
    """次日收盘数据核对项。只比较观测值，不由阈值推断资金意图或收益。

    保留参数与字段名兼容归档/答卷接口，统计档位放在 phase 明细中。
    """
    plan = []
    rates = (promo or {}).get("rates") or {}

    if "1进2" in rates and rates["1进2"].get("base", 0) > 0:
        cur = rates["1进2"]["rate"] * 100
        plan.append({
            "指标": "首板次日继续涨停比例",
            "今日基准": f"{cur:.1f}%（{rates['1进2']['promoted']}/{rates['1进2']['base']}）",
            "验证条件": f"下一交易日收盘后，与 {cur:.1f}% 比较，并同时列出继续涨停只数和首板样本数。",
            "为什么看它": "记录首板群体的次日表现，每日样本会变化。",
        })

    if (premium or {}).get("status") == "ok":
        p = premium["median_pct"]
        plan.append({
            "指标": "昨日连板股今日中位涨跌幅",
            "今日基准": f"{p:+.2f}%（{premium['n']} 只，上涨占比 {premium['win_rate'] * 100:.0f}%）",
            "验证条件": f"下一交易日收盘后，与 {p:+.2f}% 比较，同列上涨占比、样本数及缺失行情数。",
            "为什么看它": "记录连板样本的收盘涨跌幅，不计算买卖收益。",
        })

    if (gap or {}).get("status") == "ok":
        plan.append({
            "指标": "连板梯队最高高度",
            "今日基准": f"{gap['max_height']} 板"
                         + (f"，中间缺少 {gap['gaps']} 板" if gap.get("gaps") else "，中间高度无空档"),
            "验证条件": f"下一交易日收盘后，与 {gap['max_height']} 板比较高度；另查中间高度是否有空档。高度下降与中间缺档分别记录。",
            "为什么看它": "最高连板数与各高度股票数是两项独立统计。",
        })

    if (breadth or {}).get("status") == "ok":
        plan.append({
            "指标": "全市场上涨家数与中位涨跌幅",
            "今日基准": f"上涨 {breadth['up']} 家 / 下跌 {breadth['down']} 家，中位 {breadth['median_pct']:+.2f}%",
            "验证条件": f"下一交易日收盘后，与上涨 {breadth['up']} 家及中位 {breadth['median_pct']:+.2f}% 分别比较。",
            "为什么看它": "观察上涨覆盖面和中间位置个股的表现是否同时改善。",
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

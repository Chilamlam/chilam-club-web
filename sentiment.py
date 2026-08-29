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


def cycle_phase(promo: dict, gap: dict, premium: dict) -> dict:
    """
    情绪周期定位。只给「当前处于哪一段」的判断依据，不给参与建议、不给仓位。

    判据（全部来自上面的硬指标，不经过 AI）：
      1进2 晋级率是主轴——它决定次日打板资金愿不愿意继续接力
      连板溢价为负 = 昨日追高的人在亏钱，退潮特征
      梯队断层 = 高度链条断裂
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

    if r12 >= 0.45 and (prem is None or prem > 0):
        phase, desc = "发酵期", "接力顺畅，1进2 晋级率高且昨日连板仍有正溢价"
    elif r12 >= 0.30:
        phase, desc = "分歧期", "晋级率中等，资金在分歧中切换，赚钱效应不均匀"
    elif r12 >= 0.15:
        phase, desc = "退潮期", "晋级率明显走低，接力开始失败"
    else:
        phase, desc = "冰点期", "晋级率极低，打板链条基本断裂"

    if has_gap and phase in ("发酵期", "分歧期"):
        desc += "；但梯队存在断层，高度接力并不连续"

    return {
        "status": "ok",
        "phase": phase,
        "basis": desc,
        "r12": r12,
        "premium_median": prem,
        "note": "周期定位仅描述当前市场状态，不构成参与建议或仓位指引。",
    }


def verification_plan(promo: dict, gap: dict, premium: dict,
                      breadth: dict) -> list[dict]:
    """
    「明日验证条件」清单：把今晚的判断落成带**今日基准值 + 变动阈值**的可对账条目。

    为什么必须带基准值：不带基准的判断第二天无法核对，也就永远无法被证伪，
    这类「怎么说都对」的话术是信任的头号杀手。每条都写清楚
    「今天是多少 / 明天低于或高于多少算什么」，第二天可以逐条打勾。
    """
    plan = []
    rates = (promo or {}).get("rates") or {}

    if "1进2" in rates and rates["1进2"].get("reliable"):
        cur = rates["1进2"]["rate"] * 100
        plan.append({
            "指标": "1进2 晋级率",
            "今日基准": f"{cur:.1f}%（{rates['1进2']['promoted']}/{rates['1进2']['base']}）",
            "验证条件": f"明日若跌破 {max(cur - 10, 15):.0f}%，视为接力意愿转弱；"
                        f"若站上 {min(cur + 10, 60):.0f}%，视为情绪继续发酵",
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

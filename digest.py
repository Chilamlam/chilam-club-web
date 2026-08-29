# -*- coding: utf-8 -*-
"""
收盘摘要组装层（纯 stdlib + pandas，禁止 import streamlit / 禁止联网）

这一层只做一件事：把已经跑好的产物（derived.json / performance.json / 榜单 CSV）
组装成一段**可以直接读完就知道明天看什么**的文本。

设计原则（这决定推送是"有用"还是"骚扰"）：
  1. **不重复用户已经知道的东西**。不播报"今天上涨 2800 家"这种打开任何 app
     都能看到的数；只播报派生结论 + 需要跨日对账的东西。
  2. **每条判断必须带今日基准值**。推送是承诺，明天要能被逐条核对。
  3. **个性化段落放最前**。用户关心的顺序是「我的持仓 → 我该盯什么 → 大盘怎样」，
     而不是反过来。没有自选的用户直接跳过该段，不塞占位内容。
  4. **数据缺失就少说一段，绝不编**。任何一段的数据源缺失，就整段不出现，
     并在末尾统一列出「本次缺失」，让用户知道是缺数据而不是"今天没事发生"。

输出两种形态：
  build_markdown() → 邮件 / 企业微信 markdown 消息
  build_plain()    → 短文本渠道（Server酱 title、短信预览）
"""
from __future__ import annotations

import json
import os

import pandas as pd

DERIVED_PATH = os.path.join("data", "sentiment", "derived.json")
PERF_PATH = os.path.join("data", "scorecard", "performance.json")
STRONG_CSV = os.path.join("data", "strong_stocks.csv")
BREAKOUT_CSV = os.path.join("data", "breakout_stocks.csv")
LADDER_PATH = os.path.join("data", "limit_ladder.json")
SNAPSHOT_CSV = os.path.join("data", "market_snapshot.csv")

DISCLAIMER = ("本摘要由收盘后自动计算生成，仅为数据整理与结构描述，"
              "不含个股推荐、目标价、买卖点或仓位建议，不构成投资建议。")


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_csv(path: str):
    if not os.path.exists(path):
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def _bare(code) -> str:
    return str(code).strip().upper().split(".")[0]


# ================= 各段落 =================

def section_phase(derived: dict | None) -> tuple[str, str | None]:
    """情绪周期定位。返回 (markdown, 缺失原因)。"""
    if not derived or derived.get("status") == "failed":
        return "", "情绪派生指标未生成"
    ph = derived.get("phase") or {}
    if ph.get("status") != "ok":
        return "", f"周期定位不可用（{ph.get('reason', '未知')}）"

    promo = ((derived.get("promotion") or {}).get("rates") or {}).get("1进2") or {}
    prem = derived.get("premium") or {}
    gap = derived.get("ladder_gap") or {}

    lines = [f"### 📍 情绪周期：**{ph['phase']}**", f"{ph['basis']}", ""]
    if promo:
        lines.append(f"- 1进2 晋级率 **{promo['rate'] * 100:.1f}%**"
                     f"（{promo['promoted']}/{promo['base']}）"
                     + ("" if promo.get("reliable") else " ⚠️样本不足10"))
    if prem.get("status") in ("ok", "insufficient"):
        lines.append(f"- 昨日连板股今日中位涨幅 **{prem['median_pct']:+.2f}%**"
                     f"，胜率 {prem['win_rate'] * 100:.0f}%（{prem['n']} 只）")
    if gap.get("status") == "ok":
        lines.append(f"- 梯队最高 **{gap['max_height']} 板**，"
                     + (f"断层于 {gap['gaps']} 板" if gap.get("gaps") else "梯队连续")
                     + f"，今日涨停 {gap['total']} 家")
    # 周期定位的免责声明必须跟着周期一起走——它出现的地方就是最容易被当成
    # 操作指令的地方，脱开声明单独播报"发酵期"极易被读成"可以进场"。
    if ph.get("note"):
        lines += ["", f"_{ph['note']}_"]
    return "\n".join(lines), None


def section_plan(derived: dict | None) -> tuple[str, str | None]:
    """明日验证条件——这是整封摘要里唯一"明天要回来对账"的部分，必须完整。"""
    plan = (derived or {}).get("verification_plan") or []
    if not plan:
        return "", "明日验证条件为空"
    lines = ["### ✅ 明日验证条件（明天收盘逐条打勾）", ""]
    for i, it in enumerate(plan, 1):
        lines.append(f"**{i}. {it.get('指标', '')}**")
        lines.append(f"　今日基准：`{it.get('今日基准', '—')}`")
        lines.append(f"　验证条件：{it.get('验证条件', '—')}")
        lines.append("")
    return "\n".join(lines).rstrip(), None


def section_watchlist(watchlist: list[str] | None,
                      pct_map: dict | None) -> tuple[str, str | None]:
    """
    「你的池子今日」——个性化段落，放最前面。
    pct_map: {纯代码: 今日涨幅%}，由跑批层传入（本层不联网）。
    """
    if not watchlist:
        return "", None      # 没有自选不是缺失，是本来就没有，不计入 missing
    if not pct_map:
        return "", "自选股今日涨幅数据缺失"

    rows = []
    snap = _load_csv(SNAPSHOT_CSV)
    name_map = {}
    if snap is not None and not snap.empty:
        name_map = {_bare(c): n for c, n in zip(snap["ts_code"], snap["name"])}

    for code in watchlist:
        b = _bare(code)
        v = pct_map.get(b)
        if v is None:
            continue
        rows.append({"code": b, "name": name_map.get(b, b), "pct": float(v)})
    if not rows:
        return "", "自选股均未匹配到今日行情"

    df = pd.DataFrame(rows).sort_values("pct", ascending=False)
    med = float(df["pct"].median())
    up = int((df["pct"] > 0).sum())
    lines = [f"### ⭐ 你的池子今日（{len(df)} 只）", "",
             f"中位涨幅 **{med:+.2f}%**，{up} 涨 / {len(df) - up} 平跌", ""]
    head = df.head(3)
    tail = df.tail(3) if len(df) > 3 else df.iloc[0:0]
    lines.append("最强：" + "　".join(f"{r['name']} {r['pct']:+.2f}%"
                                      for _, r in head.iterrows()))
    if not tail.empty:
        lines.append("最弱：" + "　".join(f"{r['name']} {r['pct']:+.2f}%"
                                          for _, r in tail.iterrows()))
    return "\n".join(lines), None


def section_new_picks() -> tuple[str, str | None]:
    """今日新进榜标的（只报"新增"，已经在榜的不重复播报）。"""
    df = _load_csv(STRONG_CSV)
    if df is None or df.empty or "更新日期" not in df.columns:
        return "", "RPS 榜单缺失"
    latest = str(df["更新日期"].astype(str).max())
    cur = df[df["更新日期"].astype(str) == latest]
    if cur.empty:
        return "", "RPS 榜单无当日数据"

    parts = []
    if "初次入选" in cur.columns:
        new = cur[cur["初次入选"].astype(str) == latest]
        if not new.empty:
            names = "　".join(f"{r['name']}({r.get('细分行业', '-')})"
                              for _, r in new.head(8).iterrows())
            parts.append(f"- RPS 强势榜今日新进 **{len(new)}** 只：{names}"
                         + ("…" if len(new) > 8 else ""))
        else:
            parts.append("- RPS 强势榜今日无新进标的（存量轮动）")

    bk = _load_csv(BREAKOUT_CSV)
    if bk is not None and not bk.empty and "update_date" in bk.columns:
        bl = str(bk["update_date"].astype(str).max())
        bcur = bk[bk["update_date"].astype(str) == bl]
        if not bcur.empty:
            parts.append(f"- 阶段新高突破池 **{len(bcur)}** 只"
                         + (f"（榜单日期 {bl}）" if bl != latest else ""))

    if not parts:
        return "", "榜单变动无法计算"
    return "\n".join([f"### 🎯 榜单变动（{latest}）", ""] + parts), None


def section_scorecard(perf: dict | None) -> tuple[str, str | None]:
    """
    战绩快报——把"我们过去说的话准不准"主动放进推送里。
    这是反直觉但必要的：主动公示准确率（包括难看的）比只报喜更能换来续费。
    """
    if not perf or perf.get("status") == "failed":
        return "", f"战绩归档不可用（{(perf or {}).get('reason', '未生成')}）"
    strats = perf.get("strategies") or {}
    lines = []
    for _, v in strats.items():
        h = (v.get("horizons") or {}).get("5") or {}
        if h.get("status") != "ok":
            continue
        acc = h.get("direction_accuracy")
        lines.append(f"- {v.get('label')}：5日超额中位数 **{h['alpha_median']:+.2f}%**"
                     f"，跑赢基准比例 {acc * 100:.0f}%（样本 {h['n']} 条）"
                     if acc is not None else
                     f"- {v.get('label')}：5日超额中位数 **{h['alpha_median']:+.2f}%**"
                     f"（样本 {h['n']} 条）")
    if not lines:
        return "", f"战绩样本不足（{perf.get('reason', '样本积累中')}）"
    return "\n".join(["### 📊 榜单战绩（滚动统计，对基准沪深300的超额）", ""]
                     + lines
                     + ["", "口径：以上榜当日收盘为基准点，只统计超额收益。"
                        "详细分档区分度与逐日曲线见站内「战绩回看」页。"]), None


# ================= 组装 =================

def build_markdown(watchlist: list[str] | None = None,
                   pct_map: dict | None = None) -> dict:
    """
    返回 {"title","markdown","plain","missing":[...],"has_content":bool}
    任何一段缺数据就整段不出现，并汇总到 missing——让用户区分「今天没事」和「数据没到」。
    """
    derived = _load_json(DERIVED_PATH)
    perf = _load_json(PERF_PATH)
    date = (derived or {}).get("date") or ""
    date_disp = (f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else (date or "今日"))

    missing = []
    blocks = []
    for md, miss in (section_watchlist(watchlist, pct_map),
                     section_phase(derived),
                     section_plan(derived),
                     section_new_picks(),
                     section_scorecard(perf)):
        if md:
            blocks.append(md)
        if miss:
            missing.append(miss)

    ph = ((derived or {}).get("phase") or {})
    phase_txt = ph.get("phase") if ph.get("status") == "ok" else "待定"
    title = f"【Chilam Club】{date_disp} 收盘摘要 · 情绪{phase_txt}"

    body = [f"# {date_disp} 收盘摘要", ""]
    body += ["\n\n---\n\n".join(blocks)] if blocks else \
            ["今日关键数据未能全部生成，本次不做任何结论性播报。"]
    if missing:
        body += ["", "---", "", "**本次缺失（非「无事发生」，是数据未取到）**：",
                 "\n".join(f"- {m}" for m in missing)]
    body += ["", "---", "", f"_{DISCLAIMER}_"]

    md_text = "\n".join(body)
    return {
        "title": title,
        "markdown": md_text,
        "plain": build_plain(derived, perf, missing),
        "missing": missing,
        "has_content": bool(blocks),
    }


def build_plain(derived: dict | None, perf: dict | None,
                missing: list[str] | None = None) -> str:
    """短文本形态：一屏能读完，只留最硬的三个数。"""
    if not derived or derived.get("status") == "failed":
        return "今日情绪派生指标未生成，无结论性播报。"
    ph = derived.get("phase") or {}
    parts = []
    if ph.get("status") == "ok":
        parts.append(f"情绪{ph['phase']}")
    r12 = ((derived.get("promotion") or {}).get("rates") or {}).get("1进2")
    if r12:
        parts.append(f"1进2 {r12['rate'] * 100:.0f}%")
    prem = derived.get("premium") or {}
    if prem.get("status") in ("ok", "insufficient"):
        parts.append(f"连板溢价 {prem['median_pct']:+.1f}%")
    gap = derived.get("ladder_gap") or {}
    if gap.get("status") == "ok":
        parts.append(f"最高 {gap['max_height']} 板")
    n_plan = len(derived.get("verification_plan") or [])
    if n_plan:
        parts.append(f"{n_plan} 条验证条件待明日对账")
    if missing:
        parts.append(f"（{len(missing)} 项数据缺失）")
    return " | ".join(parts) if parts else "关键指标缺失，无结论性播报。"

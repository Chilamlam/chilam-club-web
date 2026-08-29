# -*- coding: utf-8 -*-
"""
短线情绪派生指标展示层（读 data/sentiment/derived.json）

只做渲染，不做计算。所有数字来自 sentiment.py 的硬计算产物，
本文件不联网、不推断、不给缺失值补默认——拿不到就显式说明拿不到。

嵌入位置：app.py 的「全市场情绪看板」，紧挨连板情绪天梯之前。
"""
from __future__ import annotations

import streamlit as st

import sentiment as sm

C_UP = "#e74c3c"      # 涨=红（A 股习惯）
C_DOWN = "#2ecc71"    # 跌=绿
C_FLAT = "#95a5a6"

PHASE_STYLE = {
    "发酵期": ("🔥", C_UP),
    "分歧期": ("⚖️", "#f39c12"),
    "退潮期": ("🌊", C_DOWN),
    "冰点期": ("🧊", "#3498db"),
}


def _pct(v, digits: int = 1, sign: bool = False) -> str:
    """None/NaN 一律显示 —，禁止用 0 冒充缺失值。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:  # NaN
        return "—"
    return f"{f:+.{digits}f}%" if sign else f"{f:.{digits}f}%"


def _render_phase(phase: dict, date: str, prev_date, generated_at) -> None:
    if not phase or phase.get("status") != "ok":
        reason = (phase or {}).get("reason", "派生指标缺失")
        st.warning(f"⚠️ 情绪周期暂不定位：{reason}")
        return

    icon, color = PHASE_STYLE.get(phase["phase"], ("📍", C_FLAT))
    st.markdown(
        f"<div style='padding:14px 18px;border-left:5px solid {color};"
        f"background:rgba(128,128,128,0.08);border-radius:6px;'>"
        f"<span style='font-size:1.6em;font-weight:700;color:{color};'>"
        f"{icon} {phase['phase']}</span>"
        f"<span style='margin-left:14px;opacity:0.85;'>{phase['basis']}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"⚠️ {phase['note']}")


def _render_kpi(derived: dict) -> None:
    promo = derived.get("promotion") or {}
    premium = derived.get("premium") or {}
    gap = derived.get("ladder_gap") or {}
    breadth = derived.get("breadth") or {}

    c1, c2, c3, c4 = st.columns(4)

    rates = promo.get("rates") or {}
    r12 = rates.get("1进2")
    with c1:
        if r12:
            st.metric(
                "1进2 晋级率",
                _pct(r12["rate"] * 100),
                delta=f"{r12['promoted']}/{r12['base']} 只",
                delta_color="off",
                help="昨日首板股中，今日成功连板的比例。首板样本大，是次日接力意愿最敏感的前瞻指标。",
            )
            if not r12.get("reliable"):
                st.caption("⚠️ 样本不足 10，比率波动大")
        else:
            st.metric("1进2 晋级率", "—")
            st.caption(promo.get("reason", "缺少昨日归档"))

    with c2:
        if premium.get("status") in ("ok", "insufficient"):
            st.metric(
                "昨日连板股今日中位涨幅",
                _pct(premium["median_pct"], 2, sign=True),
                delta=f"胜率 {premium['win_rate'] * 100:.0f}% | {premium['n']} 只",
                delta_color="off",
                help="衡量「昨天追高连板的人今天赚不赚钱」。取中位数而非均值，避免被个别妖股拉飞。",
            )
            if premium.get("reason"):
                st.caption(f"⚠️ {premium['reason']}")
            if premium.get("missing"):
                st.caption(f"（{premium['missing']} 只今日行情缺失，未计入）")
        else:
            st.metric("昨日连板股今日中位涨幅", "—")
            st.caption(premium.get("reason", "数据缺失"))

    with c3:
        if gap.get("status") == "ok":
            st.metric(
                "梯队最高高度",
                f"{gap['max_height']} 板",
                delta=(f"断层于 {gap['gaps']} 板" if gap.get("gaps") else "梯队连续"),
                delta_color="inverse" if gap.get("gaps") else "normal",
                help="高度是情绪的天花板。中间某个高度一只票都没有（断层）说明接力链条不连续。",
            )
            st.caption(f"今日涨停 {gap['total']} 家")
        else:
            st.metric("梯队最高高度", "—")
            st.caption(gap.get("reason", "数据缺失"))

    with c4:
        if breadth.get("status") == "ok":
            d = breadth["divergence"]
            st.metric(
                "均值−中位 背离",
                f"{d:+.2f} pct",
                delta=f"均值 {breadth['mean_pct']:+.2f}% / 中位 {breadth['median_pct']:+.2f}%",
                delta_color="off",
                help="均值远高于中位数 = 涨幅集中在少数标的，多数个股其实在跌，"
                     "也就是「指数涨了但我亏钱」的那种日子。",
            )
        else:
            st.metric("均值−中位 背离", "—")
            st.caption(breadth.get("reason", "数据缺失"))

    if breadth.get("status") == "ok":
        st.caption(f"📌 {breadth['verdict']}")


def _render_promotion_table(promo: dict) -> None:
    rates = (promo or {}).get("rates") or {}
    if not rates:
        return
    with st.expander("🪜 各高度晋级率明细（样本不足的档位已标注）", expanded=False):
        rows = []
        for k, v in rates.items():
            rows.append({
                "晋级路径": k,
                "昨日基数": v["base"],
                "今日晋级": v["promoted"],
                "晋级率": f"{v['rate'] * 100:.1f}%",
                "可参考性": "✅ 样本足" if v.get("reliable") else "⚠️ 样本<10，噪音大",
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption(
            "高位板（4 进 5 以上）通常只有个位数样本，比率在 0% 和 100% 之间跳，"
            "不要当信号看——这里照实列出而不是挑好看的展示。"
        )


def _render_plan(plan: list) -> None:
    if not plan:
        st.info("今日暂无可对账的验证条件（派生指标不足）。")
        return
    st.markdown("#### ✅ 明日验证条件（可逐条对账）")
    st.caption(
        "每条都带今日基准值和明确阈值。明天收盘后可以逐条打勾核对——"
        "不带基准的判断永远无法被证伪，那种「怎么说都对」的话没有价值。"
    )
    for i, item in enumerate(plan, 1):
        with st.container(border=True):
            st.markdown(f"**{i}. {item.get('指标', '')}**")
            cA, cB = st.columns([1, 1.4])
            cA.markdown(f"📍 今日基准：`{item.get('今日基准', '—')}`")
            cB.markdown(f"🎯 验证条件：{item.get('验证条件', '—')}")
            st.caption(f"💡 为什么看它：{item.get('为什么看它', '')}")


def _render_caliber(derived: dict) -> None:
    with st.expander("📖 口径与局限（建议先读一次）", expanded=False):
        st.markdown(
            f"""
**数据来源**：涨停池来自交易所公开涨停数据归档，全市场涨幅来自 Tushare 日线，
全部为收盘后硬计算，不经过任何 AI 生成或推测。

**统计日**：`{derived.get('date', '—')}`　**对比基准日**：`{derived.get('prev_date') or '—'}`　
**归档天数**：{derived.get('archive_days', 0)} 天　**产出时间**：{derived.get('generated_at', '—')}

**几个必须知道的局限**：
- **晋级率是事后统计**，它描述的是昨日板今日的接力结果，不是对明日的预测。
  它的价值在于给「情绪是在升温还是降温」一个可量化、可对账的刻度。
- **归档天数决定可比性**。归档不足 20 个交易日时，"今天 40% 是高还是低" 缺少分位参照，
  只能看绝对值，不能看历史分位。
- **连板溢价用的是当日实际涨幅**，包含开盘就跌的情况，所以它衡量的是
  「昨天收盘追进去」的处境，不是「今天低吸」的处境。
- **周期定位只描述状态，不含参与建议、目标价、止损位或仓位指引。**
  本页所有内容不构成投资建议。
"""
        )


def render_sentiment_block() -> None:
    """在全市场看板中渲染情绪派生指标区块。数据缺失时如实说明，绝不编造。"""
    st.subheader("🌡️ 短线情绪派生指标 (接力成功率与溢价)")

    derived = sm.load_derived()
    if not derived:
        st.info(
            "⏳ 情绪派生指标尚未生成。这些指标需要至少两个交易日的涨停归档才能计算，"
            "首次运行 `daily_sentiment.py` 后开始积累。"
        )
        return

    status = derived.get("status")
    if status == "failed":
        st.error("❌ 本次派生指标计算失败，关键数据缺失。以下不展示任何推测值。")
        for key, label in (("promotion", "晋级率"), ("premium", "连板溢价"),
                           ("ladder_gap", "梯队"), ("breadth", "市场宽度")):
            blk = derived.get(key) or {}
            if blk.get("status") != "ok":
                st.caption(f"· {label}：{blk.get('reason', '未知原因')}")
        return

    if status == "incomplete":
        st.warning("⚠️ 部分指标数据不完整，缺失项已在下方标注为「—」，未用旧值或估算填充。")

    st.caption(
        f"统计日 `{derived.get('date', '—')}` | 对比基准日 "
        f"`{derived.get('prev_date') or '—'}` | 归档 {derived.get('archive_days', 0)} 天"
    )

    _render_phase(derived.get("phase") or {}, derived.get("date", ""),
                  derived.get("prev_date"), derived.get("generated_at"))
    st.markdown("")
    _render_kpi(derived)
    _render_promotion_table(derived.get("promotion") or {})
    st.markdown("---")
    _render_plan(derived.get("verification_plan") or [])
    _render_caliber(derived)

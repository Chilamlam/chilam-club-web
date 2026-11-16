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


def _threshold_caption(phase: dict, th: dict) -> str:
    """
    把判定基准摊开写——不带基准的判断无法核对，也看不出档位是否失衡。

    旧版阈值（45/30/15）的问题恰恰是「看不出来」：它一样显示得漂漂亮亮，
    但 36 天里 97% 的日子被归进退潮/冰点，而页面上没有任何一处能让你发现
    门槛与真实分布不同刻度。
    """
    r12 = phase.get("r12")
    cur = f"当前晋级率 {r12 * 100:.1f}%" if isinstance(r12, (int, float)) else ""
    if th.get("method") == "percentile":
        span = f"{th.get('window_from', '—')}~{th.get('window_to', '—')}"
        return (f"📐 判定基准（自适应）：近 {th.get('samples', 0)} 个交易日晋级率分位"
                f"（{span}）→ 发酵 ≥{th['high'] * 100:.1f}% ｜ "
                f"分歧 ≥{th['mid'] * 100:.1f}% ｜ 退潮 ≥{th['low'] * 100:.1f}% ｜ "
                f"冰点 <{th['low'] * 100:.1f}%　·　{cur}")
    if th:
        return f"📐 判定基准：{th.get('note', '经验阈值')}　·　{cur}"
    return ""


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
        f"{icon} 统计档位：{phase['phase']}</span>"
        f"<span style='margin-left:14px;opacity:0.85;'>仅按连板样本分组</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    cap = _threshold_caption(phase, phase.get("thresholds") or {})
    if cap:
        st.caption(cap)
    st.caption("档位是连板样本的统计分组，不构成参与建议；未验证其预测收益的能力。")


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
                help="昨日首板股中，今日继续涨停的比例；统计截止今日收盘。",
            )
            if not r12.get("reliable"):
                st.caption("⚠️ 样本不足 10，比率波动大")
        else:
            st.metric("1进2 晋级率", "—")
            st.caption(promo.get("reason", "缺少昨日归档"))

    with c2:
        if premium.get("status") in ("ok", "insufficient"):
            st.metric(
                "昨日连板股今日中位涨跌幅",
                _pct(premium["median_pct"], 2, sign=True),
                delta=f"上涨占比 {premium['win_rate'] * 100:.0f}% | {premium['n']} 只",
                delta_color="off",
                help="昨日连板股今日收盘涨跌幅的中位数；上涨占比是样本内上涨只数比例。",
            )
            if premium.get("reason"):
                st.caption(f"⚠️ {premium['reason']}")
            if premium.get("missing"):
                st.caption(f"（{premium['missing']} 只今日行情缺失，未计入）")
        else:
            st.metric("昨日连板股今日中位涨跌幅", "—")
            st.caption(premium.get("reason", "数据缺失"))

    with c3:
        if gap.get("status") == "ok":
            st.metric(
                "梯队最高高度",
                f"{gap['max_height']} 板",
                delta=(f"断层于 {gap['gaps']} 板" if gap.get("gaps") else "梯队连续"),
                delta_color="inverse" if gap.get("gaps") else "normal",
                help="最高连板数与中间空档分别统计；最高板降低不等于出现中间空档。",
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
                help="全市场个股涨跌幅的算术均值减中位数，单位为百分点。不是指数收益或权重贡献。",
            )
        else:
            st.metric("均值−中位 背离", "—")
            st.caption(breadth.get("reason", "数据缺失"))

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
    st.markdown("#### 次日收盘核对项")
    st.caption("下一交易日收盘后更新，用于比较两日统计。")
    for i, item in enumerate(plan, 1):
        with st.container(border=True):
            st.markdown(f"**{i}. {item.get('指标', '')}**")
            cA, cB = st.columns([1, 1.4])
            cA.markdown(f"📍 今日基准：`{item.get('今日基准', '—')}`")
            cB.markdown(f"🎯 验证条件：{item.get('验证条件', '—')}")
            st.caption(f"💡 为什么看它：{item.get('为什么看它', '')}")


def _render_caliber(derived: dict) -> None:
    with st.expander("📖 统计口径", expanded=False):
        st.markdown(
            f"""
**数据来源**：涨停池来自交易所公开涨停数据归档，全市场涨幅来自 Tushare 日线，
全部为收盘后硬计算，不经过任何 AI 生成或推测。

**统计日**：`{derived.get('date', '—')}`　**对比基准日**：`{derived.get('prev_date') or '—'}`　
**归档天数**：{derived.get('archive_days', 0)} 天　**产出时间**：{derived.get('generated_at', '—')}

- 晋级率统计昨日首板股在今日继续涨停的比例。
- 连板样本涨跌幅以昨日收盘为基准；上涨占比为样本中涨幅大于零的比例，未模拟实际买卖。
- 档位使用此前最多60个交易日的晋级率分位数，至少需要 {sm.PHASE_MIN_SAMPLES} 个有效交易日；不足时使用注明的经验阈值。
- 中间高度无股票称为缺档，与最高连板数下降分别记录。
"""
        )


def render_sentiment_block() -> None:
    """在全市场看板中渲染情绪派生指标区块。数据缺失时如实说明，绝不编造。"""
    st.subheader("🌡️ 短线接力统计")

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

    _render_kpi(derived)
    _render_promotion_table(derived.get("promotion") or {})
    with st.expander("统计档位与次日收盘核对", expanded=False):
        _render_phase(derived.get("phase") or {}, derived.get("date", ""),
                      derived.get("prev_date"), derived.get("generated_at"))
        _render_plan(sm.verification_plan(
            derived.get("promotion") or {}, derived.get("ladder_gap") or {},
            derived.get("premium") or {}, derived.get("breadth") or {},
        ))
    _render_caliber(derived)

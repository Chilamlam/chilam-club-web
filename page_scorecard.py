# -*- coding: utf-8 -*-
"""
战绩回看页（Scorecard）

存在意义：本站所有榜单此前从不回看，用户无从判断「上周推的票后来怎么样」。
这一页把榜单的历史表现摊开在明面上，包括不好看的部分——
可验证的记录才是续费的唯一理由，漂亮但无法核对的数字反而消耗信任。

三条硬规矩（对标 TradingAgents-astock 的 performance 子命令）：
1. 只统计超额收益 alpha（对沪深300），绝对收益在牛市里人人都对，没有信息量。
2. 主口径用中位数而非均值——均值会被个别妖股拉飞，中位数才是「随手买一只」的体验。
3. 样本不足就明写「基本是噪音，不构成参考」，绝不用小样本充当业绩证明。
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import scorecard as sc


def _pct(v, digits: int = 2) -> str:
    """把小数转成百分比字符串。None/NaN 一律显示 —，禁止显示 0 冒充有数据。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:  # NaN
        return "—"
    return f"{f * 100:+.{digits}f}%"


def _status_badge(status: str) -> str:
    return {
        "ok": "✅ 样本充足",
        "insufficient": "⚠️ 样本不足",
        "failed": "❌ 数据缺失",
        "complete": "✅ 完整",
        "incomplete": "⚠️ 不完整",
    }.get(str(status), str(status))


def _render_caliber() -> None:
    """口径说明。教用户怎么读这个数，而不是自证清白。"""
    with st.expander("📐 口径说明：这几个数该怎么读（建议先看）", expanded=False):
        st.markdown(
            f"""
**基准**：沪深300（`{sc.BENCHMARK}`）。表里所有收益都是**超额收益**，
即「个股涨幅 − 同期沪深300 涨幅」。为什么不看绝对涨幅？因为大盘涨 5% 的那周，
随便买什么都赚钱，绝对收益证明不了榜单有用。

**T+N 的起点**：以**上榜当日收盘价**为基准点。你在当晚看到榜单，次日开盘才能买，
所以这个口径**不等于你实际能拿到的收益**——它衡量的是「榜单的排序有没有信息量」，
而不是「照着买能赚多少」。这一点必须先说清楚。

**为什么用中位数**：均值会被一两只翻倍妖股拉飞，看起来很美但复现不了。
中位数代表「从榜单里随手挑一只」的典型体验，更接近你的真实处境。

**方向正确率**：榜单是看多信号，所以只有**跑赢沪深300** 才算方向正确。
跟着大盘一起涨但没跑赢，记为错。

**区分度检验**：如果排序真有信息量，前 10 名的表现应当好于 11-30 名，
后者应当好于 31-50 名。若这个顺序不成立，说明排序本身没有区分能力——
这条自检比任何漂亮数字都重要，不通过我也会照实写在下面。

**样本门槛**：单组样本少于 {sc.MIN_SAMPLE} 条，一律标注为噪音。
统计学上这个量级的样本什么都证明不了，拿来当业绩宣传是不诚实的。
            """
        )


def _render_strategy(key: str, entry: dict) -> None:
    label = entry.get("label", key)
    st.markdown(f"#### {label}")
    st.caption(f"累计归档 {entry.get('total_picks', 0)} 条上榜记录，覆盖 {entry.get('days', 0)} 个交易日")

    horizons = entry.get("horizons", {})
    cols = st.columns(len(sc.HORIZONS))
    for col, n in zip(cols, sc.HORIZONS):
        blk = horizons.get(str(n), {})
        status = blk.get("status")
        with col:
            st.markdown(f"**T+{n} 交易日**")
            if status == "failed":
                st.error(f"❌ {blk.get('reason', '数据缺失')}")
                continue
            st.metric(
                "超额收益中位数",
                _pct(blk.get("alpha_median")),
                help="个股涨幅 − 同期沪深300 涨幅，取中位数",
            )
            acc = blk.get("direction_accuracy")
            st.metric(
                "方向正确率",
                _pct(acc, 1) if acc is None else f"{float(acc) * 100:.1f}%",
                help=f"跑赢沪深300 的比例（{blk.get('win_count', 0)}/{blk.get('n', 0)}）",
            )
            st.caption(
                f"样本 {blk.get('n', 0)} 条 · {_status_badge(status)}\n\n"
                f"四分位 {_pct(blk.get('alpha_p25'))} ~ {_pct(blk.get('alpha_p75'))}"
            )
            if status == "insufficient":
                st.warning(blk.get("reason", "样本不足，基本是噪音"), icon="⚠️")

    # --- 区分度检验 ---
    disc = entry.get("discrimination", {})
    st.markdown("**排序区分度检验**")
    if disc.get("status") == "ok":
        buckets = pd.DataFrame(disc.get("buckets", []))
        if not buckets.empty:
            buckets["超额中位数"] = buckets["alpha_median"].map(lambda v: _pct(v))
            buckets = buckets.rename(columns={"bucket": "榜内档位", "n": "样本数"})
            st.dataframe(
                buckets[["榜内档位", "样本数", "超额中位数"]],
                hide_index=True, use_container_width=True,
            )
        if disc.get("monotonic"):
            st.success(f"✅ {disc.get('verdict')}")
        else:
            st.error(
                f"❌ {disc.get('verdict')}\n\n"
                "这说明目前这份榜单更像是「一篮子强势股」，靠前名次并不代表更好，"
                "取前 10 名和取前 50 名的预期差别不大。我把它照实写在这里，"
                "而不是只挑好看的数字展示。"
            )
    else:
        st.info(f"⏳ {disc.get('reason', '样本不足，暂时无法检验区分度')}")

    # --- 逐日 alpha 曲线 ---
    daily = entry.get("daily_alpha", [])
    if len(daily) >= 3:
        df = pd.DataFrame(daily)
        df["alpha_median"] = pd.to_numeric(df["alpha_median"], errors="coerce").astype("float64")
        df = df.dropna(subset=["alpha_median"])
        if not df.empty:
            fig = go.Figure()
            colors = ["#e74c3c" if v >= 0 else "#2ecc71" for v in df["alpha_median"]]
            fig.add_trace(go.Bar(
                x=df["date"], y=df["alpha_median"] * 100,
                marker_color=colors, name="当日上榜组超额收益中位数",
                hovertemplate="%{x}<br>超额 %{y:.2f}%<extra></extra>",
            ))
            fig.add_hline(y=0, line_color="#888", line_width=1)
            fig.update_layout(
                height=280, margin=dict(l=10, r=10, t=30, b=10),
                title=f"每个上榜日往后 T+{sc.HORIZONS[min(1, len(sc.HORIZONS) - 1)]} 的超额收益中位数（%）",
                yaxis_title="超额收益 %", showlegend=False,
                template="plotly_white",
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption("红柱=该日上榜组跑赢沪深300，绿柱=跑输。柱子越均匀分布在零轴上方，策略越稳定。")


def render_scorecard_page() -> None:
    st.header("🎯 战绩回看：这些榜单到底准不准")
    st.caption("把历史榜单的真实表现摊开，包括不好看的部分。可验证的记录才值得付费。")

    perf = sc.load_performance()
    if perf is None:
        st.info(
            "⏳ 战绩归档尚未生成。\n\n"
            "该页依赖 `daily_scorecard.py` 每日归档榜单快照与复权价格，"
            "首次需要跑一次历史回溯来积累样本。跑批完成后此处会自动出现统计结果。"
        )
        _render_caliber()
        return

    status = perf.get("status")
    if status == "failed":
        st.error(f"❌ 战绩统计失败：{perf.get('reason', '未知原因')}")
        st.caption("关键数据拿不到时这里会直接报错，而不是拿旧值或估算值凑一个数出来。")
        _render_caliber()
        return

    arc = perf.get("archive", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("归档区间", f"{arc.get('date_from', '—')} → {arc.get('date_to', '—')}")
    c2.metric("覆盖交易日", f"{arc.get('trade_days', 0)} 天")
    c3.metric("上榜记录", f"{arc.get('pick_rows', 0)} 条")
    c4.metric("统计状态", _status_badge(status))

    if status == "incomplete":
        st.warning(
            f"⚠️ {perf.get('reason', '样本不足')}\n\n"
            "样本还在积累中。这个阶段的数字只能当占位，别拿它做决策依据。",
            icon="⚠️",
        )

    _render_caliber()
    st.markdown("---")

    strategies = perf.get("strategies", {})
    if not strategies:
        st.info("暂无任何策略的归档记录。")
        return

    order = [k for k in ("rps", "breakout", "etf", "radar") if k in strategies]
    order += [k for k in strategies if k not in order]
    tabs = st.tabs([sc.STRATEGY_LABELS.get(k, k) for k in order])
    for tab, k in zip(tabs, order):
        with tab:
            _render_strategy(k, strategies[k])

    st.markdown("---")
    st.caption(
        f"统计生成时间 {perf.get('generated_at', '—')} · 基准 {perf.get('benchmark')} · "
        f"样本门槛 {perf.get('min_sample')} 条。"
        "本页仅为历史统计，不构成投资建议，历史表现不代表未来收益。"
    )

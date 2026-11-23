"""历史榜单表现：结果、范围与计算假设分开展示。"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import scorecard as sc


def _pct(v, digits=2):
    try:
        value = float(v)
        return "—" if not pd.notna(value) else f"{value * 100:+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _render_caliber():
    with st.expander("统计口径"):
        st.write("以上榜当日收盘为起点，用复权价核对 T+1、T+5、T+10 个交易日后的变化。"
                 "超额为个股涨跌幅减同期沪深300涨跌幅；跑赢占比不等于交易胜率。"
                 "榜单收盘后生成，未计手续费、滑点和无法成交的情形。")
        st.write("记录条数按股票和上榜日计数，同一股票可重复上榜。中位数、四分位数描述已归档记录，"
                 "不按记录数量判定参考价值，也不据此认定未来策略优势。")


def _render_strategy(key, entry):
    st.caption(f"累计 {entry.get('total_picks', 0)} 条上榜记录 · {entry.get('days', 0)} 个入选日")
    cols = st.columns(len(sc.HORIZONS))
    for col, n in zip(cols, sc.HORIZONS):
        blk = entry.get("horizons", {}).get(str(n), {})
        with col:
            st.markdown(f"**T+{n} 交易日**")
            st.metric("相对沪深300的中位差值", _pct(blk.get("alpha_median")))
            count = blk.get("n", 0)
            wins = blk.get("win_count", 0)
            st.caption(f"已核对 {count} 条 · 跑赢 {wins} 条" if count else "暂无到期且价格完整的记录")
            if count:
                st.caption(f"跑赢占比 {wins/count:.1%} · 四分位 {_pct(blk.get('alpha_p25'))} 至 {_pct(blk.get('alpha_p75'))}")
    disc = entry.get("discrimination") or {}
    buckets = disc.get("buckets") or []
    if buckets:
        st.subheader("不同榜内名次的后续表现")
        st.dataframe(pd.DataFrame([{"名次范围": r["bucket"], "已核对记录": r["n"],
            "超额中位数": _pct(r["alpha_median"])} for r in buckets]), hide_index=True, width="stretch")
    daily = entry.get("daily_alpha") or []
    if daily:
        df = pd.DataFrame(daily)
        fig = go.Figure(go.Bar(x=df["date"], y=df["alpha_median"]*100,
            marker_color=["#dc6565" if v >= 0 else "#2a9d8f" for v in df["alpha_median"]]))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=40, b=10),
                          title="逐个上榜日的 T+5 超额中位数", yaxis_title="百分点")
        st.plotly_chart(fig, width="stretch")
        st.caption("相邻上榜日的观察窗口可能重叠；每根柱子不是一次独立交易。")
    with st.expander("回归与间隔抽取结果"):
        e = entry.get("effective_sample") or {}
        st.write(f"覆盖 {e.get('n_days', 0)} 个有效入选日，间隔抽取 {e.get('n_independent', 0)} 组，"
                 f"其中 {e.get('win_independent', 0)} 组跑赢沪深300。")
        st.caption("按每5个有效入选日抽取一组，减少窗口重叠；共同市场因素仍可能导致相关，不视为独立试验。")
        b = entry.get("beta") or {}
        if b.get("status") == "ok":
            cols = st.columns(3)
            cols[0].metric("回归 beta", f"{b['beta']:.2f}")
            cols[1].metric("拟合 R²", f"{b['r_squared']:.2f}")
            cols[2].metric("扣除 beta × 基准后的中位数", _pct(b.get("adj_alpha_median")))
            st.caption(f"回归覆盖 {b.get('n_days', 0)} 个入选日。单因子线性拟合不等于因果归因，剩余部分不等于选股能力。")
        else:
            st.caption(f"当前未计算回归；需至少 {sc.MIN_DAYS_BETA} 个可用入选日及有变化的基准收益。")


def render_scorecard_page():
    st.header("历史榜单表现")
    perf = sc.load_performance()
    if not perf or perf.get("status") == "failed":
        st.info("暂无可核对的榜单与价格归档。" if not perf else perf.get("reason", "归档数据缺失"))
        _render_caliber()
        return
    arc = perf.get("archive") or {}
    st.caption(f"归档 {arc.get('date_from', '—')} → {arc.get('date_to', '—')} · "
               f"{arc.get('trade_days', 0)} 个交易日 · {arc.get('pick_rows', 0)} 条记录")
    strategies = perf.get("strategies") or {}
    if strategies:
        selected = st.selectbox("榜单", list(strategies), format_func=lambda k: sc.STRATEGY_LABELS.get(k, k))
        _render_strategy(selected, strategies[selected])
    _render_caliber()
    st.caption(f"统计生成于 {perf.get('generated_at', '—')} · 基准 {perf.get('benchmark', sc.BENCHMARK)}")

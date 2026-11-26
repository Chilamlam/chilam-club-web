"""PE history, implied-price bands and explicit earnings scenarios for an equity."""
from __future__ import annotations

import datetime as dt
import logging

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import pe_band as model
from pe_band_data import fetch_history


@st.cache_data(ttl=3600, max_entries=120, show_spinner=False)
def _load_cached(sym, years, cutoff, name):
    # cutoff is part of the cache key so the next publication window cannot reuse yesterday's range.
    # Raise on failure so a temporary network error is not cached for an hour.
    return fetch_history(sym, years, name=name)


def _load(sym, years, cutoff, name):
    try:
        return _load_cached(sym, years, cutoff, name), ""
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:
        logging.getLogger(__name__).warning("PE history unavailable: %s", type(exc).__name__)
        return None, "历史估值来源暂时无法读取，请稍后刷新。"


def draw_bands(frame, multiples, currency):
    chart = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.08, row_heights=[.72, .28],
                          subplot_titles=("股价与 PE 估值带", "历史 PE(TTM)"))
    colors = ["#16a085", "#3498db", "#8e6bce", "#d39432", "#cf677b", "#7a8a99", "#6155a6"]
    for i, (label, _) in enumerate(multiples):
        chart.add_trace(go.Scatter(x=frame.date, y=frame[label], name=label, connectgaps=False,
                                  mode="lines+markers", marker=dict(size=2),
                                  line=dict(color=colors[i], width=1.5),
                                  hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f} " + currency + "<extra>%{fullData.name}</extra>"), row=1, col=1)
    chart.add_trace(go.Scatter(x=frame.date, y=frame.close, name="实际收盘价", line=dict(color="#263c50", width=2.5),
                              hovertemplate="%{x|%Y-%m-%d}<br>收盘 %{y:,.2f} " + currency + "<extra></extra>"), row=1, col=1)
    chart.add_trace(go.Scatter(x=frame.date, y=frame.pe_ttm.where(frame.usable), name="PE(TTM)",
                              mode="lines+markers", marker=dict(size=2),
                              connectgaps=False, showlegend=False, line=dict(color="#5967bf", width=1.5),
                              hovertemplate="%{x|%Y-%m-%d}<br>PE %{y:.2f} 倍<extra></extra>"), row=2, col=1)
    for i, (_, multiple) in enumerate(multiples):
        chart.add_hline(y=multiple, line_color=colors[i], line_width=.6, line_dash="dot", row=2, col=1)
    chart.update_yaxes(title_text=f"价格（{currency}，未复权）", row=1, col=1)
    chart.update_yaxes(title_text="PE（倍）", row=2, col=1)
    chart.update_xaxes(type="date")
    chart.update_layout(height=690, margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified",
                        legend=dict(orientation="h", yanchor="bottom", y=1.07, x=0), template="plotly_white")
    return chart


def render_pe_band(sym: dict, quote: dict | None = None):
    quote = quote or {}
    st.subheader("PE Band · 个股估值带")
    st.caption("把当前估值放回自己的历史中，再检查盈利能否维持。支持 A 股、港股、美股的公司股票。")
    try:
        market, code = model.equity_identity(sym, quote.get("name", ""))
    except ValueError as exc:
        st.info(str(exc))
        return
    scope = f"pe_{market}_{code}"
    c1, c2, c3 = st.columns([1, 2, 1])
    years = c1.radio("历史区间", (1, 3, 5), index=1, format_func=lambda y: f"近 {y} 年", horizontal=True, key=scope+"_years")
    mode = c2.radio("估值线", ["历史分位倍数", "自定 PE 倍数"], horizontal=True, key=scope+"_mode")
    c3.write("")
    if c3.button("更新估值数据", key=scope+"_refresh"):
        _load_cached.clear()
    custom = st.text_input("PE 倍数（逗号分隔）", "10, 15, 20, 25, 30", key=scope+"_multiples") if mode == "自定 PE 倍数" else None
    cutoff = model.completed_cutoff(market).isoformat()
    with st.spinner("读取历史 PE 与同日收盘价…"):
        snapshot, error = _load(sym, years, cutoff, quote.get("name", ""))
    if error:
        st.warning(error)
        st.caption("公开来源并非覆盖所有股票；缺失时保留空白，可继续使用行情与技术图。")
        return
    frame, coverage = snapshot["frame"], snapshot["coverage"]
    try:
        multiples = model.band_multiples(frame, custom)
    except ValueError as exc:
        st.warning(str(exc))
        return
    summary = model.latest_summary(frame)
    currency = snapshot["currency"]
    pe = summary.get("pe_ttm")
    a, b, c, d = st.columns(4)
    a.metric(f"参考收盘价 · {currency}", f"{summary['close']:,.2f}")
    b.metric("PE(TTM)", f"{pe:.2f} 倍" if summary["usable"] else "不适用 / 缺失")
    c.metric(f"近 {years} 年 PE 分位", f"{summary['percentile']:.1f}%" if summary["usable"] else "—",
             help="在所选区间的正 PE 交易日样本中，低于本次 PE 的比例；相同数值按一半计入。不是胜率或上涨概率。")
    d.metric("正 PE 样本", f"{coverage['positive_days']} / {coverage['price_days']} 日")
    st.caption(f"参考日期 {summary['date']} · 实际价格覆盖 {coverage['start']} 至 {coverage['end']} · 币种 {currency} · 未复权收盘价。")
    gap_days = (dt.date.fromisoformat(snapshot["cutoff"]) - dt.date.fromisoformat(summary["date"])).days
    if gap_days > 4:
        st.warning(f"最新价格距本次查询截止日已有 {gap_days} 个自然日，可能是停牌或来源延迟；以下使用旧资料。")
    if not summary["usable"]:
        st.info("最新价格日的 PE 为非正数或缺失，当前估值分位与价格情景不计算。下图仍保留过去有效的区间。")
    if coverage["nonpositive_days"] or coverage["missing_days"]:
        st.caption(f"区间内非正 PE {coverage['nonpositive_days']} 日，PE 缺失 {coverage['missing_days']} 日；这些日期的估值线留空，不补值。分位仅代表已取得的正 PE 样本。")
    if not multiples:
        st.info("可用的正 PE 样本不足 2 日，暂不生成历史分位估值线。")
    plot_frame = model.band_prices(frame, multiples)
    st.plotly_chart(draw_bands(plot_frame, multiples, currency), width="stretch", key=scope+"_chart")
    if custom is None:
        st.caption("P10 / P25 / P50 / P75 / P90：所选区间正 PE 的历史分位倍数。整张图固定使用这五个倍数回看，不能当作当时已知的买卖信号。")
    st.caption("估值线 = 当日未复权收盘价 ÷ 当日 PE(TTM) × 指定 PE 倍数。拆股、送转、分红等可能造成未复权图形跳变。")

    if summary["usable"] and multiples:
        st.markdown("#### 如果盈利变了，这些估值对应什么价格？")
        change = st.slider("同口径每股盈利变化假设（%）", -80, 100, 0, 5, key=scope+"_earnings")
        st.caption(f"基于 {summary['date']} 的收盘价和 PE 反推每股盈利，假设股本口径不变；{change:+d}% 是你设定的情景，不是盈利预测。")
        table = model.scenario_table(summary, multiples, change)
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "PE（倍）": st.column_config.NumberColumn(format="%.2f"),
            "对应价格": st.column_config.NumberColumn(f"对应价格（{currency}）", format="%.2f"),
            "相对参考收盘价（%）": st.column_config.NumberColumn(format="%+.1f%%"),
        })
        st.write("先核对财报中的盈利是否可持续，再决定愿意支付的倍数。周期盈利高点也可能出现低 PE；历史低分位本身不是买入理由。")

    with st.expander("数据来源、覆盖与计算口径"):
        st.write(snapshot["source"])
        for label, url in snapshot["links"].items():
            st.markdown(f"[{label}]({url})")
        stamp = dt.datetime.fromisoformat(snapshot["retrieved_at"]).astimezone(dt.timezone(dt.timedelta(hours=8)))
        st.caption(f"读取时间 {stamp:%Y-%m-%d %H:%M}（北京时间）· 缓存最多 1 小时。当地时间 18:00 前不纳入当天记录；日期以来源实际发布为准。")
        st.write(f"请求区间 {snapshot['requested_start']} 至 {snapshot['cutoff']}。"
                 f"仅对齐真实价格日期；未与价格日期对应的估值记录 {coverage['unmatched_valuation_days']} 条已排除。")
        st.write("只采用来源标注的 PE(TTM)，不混用静态 PE 或预测 PE。港美股的价格与 PE 来自不同提供商，"
                 "其利润、稀释股数与历史修订口径可能不同，不宜据此直接比较不同市场谁更便宜。"
                 "收盘价 / PE 是同币种的隐含每股盈利，不是另行核验的财报 EPS；ADR 按交易单位计算。")
        st.write("负数、零、缺失 PE 不进入分位样本；正 PE 的极端值保留。"
                 "公开历史序列可能修订或抽样，不能保证还原当时已披露的信息，也不能直接用于无前视偏差的回测。")
        export = plot_frame.drop(columns=["pe_observed", "usable"]).copy()
        export["currency"] = currency
        export["price_basis"] = snapshot["price_basis"]
        export["source"] = snapshot["source"]
        export["retrieved_at"] = snapshot["retrieved_at"]
        st.download_button("下载本次估值数据 CSV", export.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"pe_band_{code}_{summary['date']}.csv", mime="text/csv", key=scope+"_download")

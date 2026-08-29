"""
技术分析图层与 UI —— 把 tech_analysis 的计算结果画到 Plotly 图上并给出文字结论。

分工：tech_analysis.py 只算不画（纯 pandas/numpy），本模块只画不算（plotly/streamlit）。
这样计算逻辑可以脱离 Streamlit runtime 单独测试。
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import tech_analysis as ta

# 缠论
C_STROKE = "#8e44ad"        # 笔
C_SEGMENT = "#e67e22"       # 线段
C_PIVOT = "rgba(52,152,219,0.13)"
C_PIVOT_LINE = "#3498db"
# 帝纳波利
C_F3 = "#e74c3c"
C_F5 = "#c0392b"
C_TARGET = "#27ae60"
C_CONF = "rgba(241,196,15,0.20)"

_MODE_CHAN = "缠论结构"
_MODE_DINAPOLI = "帝纳波利"
ANALYSIS_MODES = [_MODE_CHAN, _MODE_DINAPOLI]


def _xs(df: pd.DataFrame, i: int):
    """把 K 线下标换成 category 型 X 轴上的标签。越界时夹回边界。"""
    i = max(0, min(int(i), len(df) - 1))
    return df["datetime"].iloc[i]


# ==================== 缠论图层 ====================

def draw_chan(fig: go.Figure, df: pd.DataFrame, res: dict, show_stroke: bool = True):
    """画笔连线、线段连线、中枢矩形。"""
    if show_stroke and res.get("strokes"):
        pts = res["strokes"]
        fig.add_trace(go.Scatter(
            x=[_xs(df, p["i"]) for p in pts], y=[p["price"] for p in pts],
            mode="lines+markers", name=f"笔（{len(pts)} 个端点）",
            line=dict(color=C_STROKE, width=1.3, dash="dot"),
            marker=dict(size=4, color=C_STROKE),
            hovertemplate="笔端点 %{y}<extra></extra>"))

    if res.get("segments"):
        seg = res["segments"]
        fig.add_trace(go.Scatter(
            x=[_xs(df, p["i"]) for p in seg], y=[p["price"] for p in seg],
            mode="lines+markers", name=f"线段（{len(seg)} 个端点）",
            line=dict(color=C_SEGMENT, width=2.2),
            marker=dict(size=7, color=C_SEGMENT, symbol="diamond"),
            hovertemplate="线段端点 %{y}<extra></extra>"))

    for idx, pv in enumerate(res.get("pivots", [])):
        alive = pv.get("alive")
        fig.add_shape(
            type="rect", xref="x", yref="y",
            x0=_xs(df, pv["start_i"]), x1=_xs(df, pv["end_i"]),
            y0=pv["zd"], y1=pv["zg"],
            fillcolor=C_PIVOT if alive else "rgba(149,165,166,0.10)",
            line=dict(color=C_PIVOT_LINE if alive else "#95a5a6",
                      width=1.6 if alive else 1, dash="solid" if alive else "dot"),
            layer="below")
        if alive:
            fig.add_annotation(
                x=_xs(df, pv["end_i"]), y=pv["zg"],
                text=f"中枢 {pv['legs']}段", showarrow=False,
                font=dict(size=10, color=C_PIVOT_LINE),
                yshift=10, xanchor="right")


# ==================== 帝纳波利图层 ====================

def draw_dinapoli(fig: go.Figure, df: pd.DataFrame, res: dict):
    """画 DMA（含右移悬空段）、F3/F5 回撤位、COP/OP/XOP 目标位、汇聚区。"""
    x_all = list(df["datetime"])

    # DMA：右移意味着尾部若干点落在 K 线之外，用 "T+1"… 这类虚拟标签接上
    for name, spec in res.get("dma", {}).items():
        fut = spec["future"]
        xs = x_all + [f"T+{j}" for j in range(1, fut + 1)]
        ys = spec["y"]
        m = min(len(xs), len(ys))
        fig.add_trace(go.Scatter(
            x=xs[:m], y=ys[:m], mode="lines", name=f"{name} DMA",
            line=dict(color=spec["color"], width=1.6),
            connectgaps=False,
            hovertemplate=f"{name} DMA %{{y}}<extra></extra>"))

    # Fibnode 回撤位：只画最近一段，避免线太多
    nodes = res.get("fibnodes") or []
    if nodes:
        nd = nodes[-1]
        for lbl, price, color in (("F3 0.382", nd["f3"], C_F3), ("F5 0.618", nd["f5"], C_F5)):
            fig.add_hline(y=price, line=dict(color=color, width=1.2, dash="dash"),
                          annotation_text=f"{lbl} {ta._n(price)}",
                          annotation_position="right",
                          annotation_font=dict(size=10, color=color))

    tg = res.get("targets") or {}
    if tg:
        for lbl, key in (("COP", "cop"), ("OP", "op"), ("XOP", "xop")):
            fig.add_hline(y=tg[key], line=dict(color=C_TARGET, width=1.1, dash="dashdot"),
                          annotation_text=f"{lbl} {ta._n(tg[key])}",
                          annotation_position="left",
                          annotation_font=dict(size=10, color=C_TARGET))
        # ABC 三点连线，让用户看清目标位是怎么推出来的
        fig.add_trace(go.Scatter(
            x=[_xs(df, tg["a_i"]), _xs(df, tg["b_i"]), _xs(df, tg["c_i"])],
            y=[tg["a"], tg["b"], tg["c"]],
            mode="lines+markers+text", name="ABC 摆动",
            text=["A", "B", "C"], textposition="top center",
            line=dict(color=C_TARGET, width=1.8),
            marker=dict(size=9, color=C_TARGET, symbol="circle-open")))

    for c in res.get("confluence", []):
        fig.add_hrect(y0=c["lo"], y1=c["hi"], fillcolor=C_CONF, line_width=0,
                      layer="below",
                      annotation_text=f"汇聚区 {ta._n(c['price'])}",
                      annotation_position="top left",
                      annotation_font=dict(size=10, color="#b7950b"))


# ==================== MACD 副图 ====================

def draw_macd(df: pd.DataFrame, res: dict) -> go.Figure:
    macd = res.get("macd")
    if macd is None or macd.empty:
        return go.Figure()
    x = list(df["datetime"])
    colors = ["#ff4d4d" if v >= 0 else "#2ecc71" for v in macd["hist"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=x, y=macd["hist"], name="MACD 柱",
                         marker_color=colors, opacity=0.75))
    fig.add_trace(go.Scatter(x=x, y=macd["dif"], name="DIF",
                             line=dict(color="#f39c12", width=1.4)))
    fig.add_trace(go.Scatter(x=x, y=macd["dea"], name="DEA",
                             line=dict(color="#3498db", width=1.4)))
    for dv in res.get("divergence", []):
        fig.add_annotation(x=_xs(df, dv["i1"]), y=dv["d1"], text=dv["type"],
                           showarrow=True, arrowhead=2, arrowsize=0.8,
                           font=dict(size=10, color="#e74c3c"),
                           arrowcolor="#e74c3c", ay=-28)
    fig.update_layout(
        title="MACD (8, 17, 9) —— 帝纳波利偏好的快参数",
        xaxis=dict(type="category", tickangle=-45, nticks=12,
                   gridcolor="rgba(128,128,128,0.15)"),
        yaxis=dict(gridcolor="rgba(128,128,128,0.15)"),
        height=250, margin=dict(t=40, b=30, l=10, r=10),
        hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", y=1.18, x=0),
        barmode="relative")
    return fig


# ==================== 文字结论区 ====================

_DISCLAIMER = ("以上全部由程序按固定规则自动推导，**不构成任何投资建议**。"
               "缠论的笔/线段/中枢与帝纳波利的摆动点选取都依赖参数口径，"
               "不同软件画法会有差异；技术分析只描述已发生的价格结构，不预测未来。")


def render_conclusion(res: dict, period: str, modes: list):
    """图下方的文字结论。modes 控制显示哪几套体系的解读。"""
    st.markdown("#### 🧭 自动技术分析结论")

    stt = res.get("state", {})
    cols = st.columns(len(modes)) if len(modes) > 1 else [st.container()]

    for col, mode in zip(cols, modes):
        with col:
            if mode == _MODE_CHAN:
                st.markdown("**缠论结构**")
                st.info(stt.get("chan", "—"))
                pv = (res.get("pivots") or [None])[-1]
                if pv:
                    width = (pv["zg"] - pv["zd"]) / pv["zd"] * 100 if pv["zd"] else 0.0
                    status = "仍在延伸中" if pv.get("alive") \
                        else f"已离开 {pv.get('legs_after', 0)} 段"
                    st.caption(
                        f"中枢区间 ZD {ta._n(pv['zd'])} ~ ZG {ta._n(pv['zg'])}"
                        f"（宽度 {width:.2f}%）｜已走 {pv['legs']} 段｜{status}")
                st.caption(f"笔 {len(res.get('strokes', []))} 个端点 ／ "
                           f"线段 {len(res.get('segments', []))} 个端点 ／ "
                           f"中枢 {len(res.get('pivots', []))} 个"
                           f"（原始 {res.get('n_bars')} 根 K 线包含处理后 {res.get('merged')} 根）")
            else:
                st.markdown("**帝纳波利**")
                st.info(stt.get("dinapoli", "—"))
                st.success(stt.get("target", "—"))

    for w in stt.get("warn", []):
        st.warning(w)

    levels = res.get("levels") or []
    if levels:
        st.markdown("**关键价位（按距现价远近排序）**")
        tbl = pd.DataFrame([{
            "价位": ta._n(x["price"]),
            "类型": x["label"],
            "方向": x["side"],
            "距现价": f"{x['gap_pct']:+.2f}%",
        } for x in levels])
        st.dataframe(tbl, use_container_width=True, hide_index=True)

    st.caption(_DISCLAIMER)

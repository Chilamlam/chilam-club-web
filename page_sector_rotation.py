"""同花顺当日涨停归因轮动；东财概念指数作为独立的比较视图。"""
from __future__ import annotations

import html as _html

import pandas as pd
import plotly.express as px
import streamlit as st

import sector_rotation as sr
from ui_compat import html_embed
from site_data import load_csv
from theme_activity_ui import render_activity

ANALYSIS_PATH = "data/sector_rotation/analysis.json"
HISTORY_PATH = "data/sector_rotation/history.csv"

_SOURCE_NOTE = (
    "口径：东财概念板块（已排除指数成分、风格和持股等属性集合）。"
    "板块范围与其他平台可能不同；重合度只表示近10日涨幅榜的成员稳定程度。"
)


def load_analysis() -> dict | None:
    hist=load_csv(HISTORY_PATH)
    return _analyze(hist) if hist is not None and not hist.empty else None


@st.cache_data(ttl=600,show_spinner=False)
def _analyze(hist):
    return sr.compute_analysis(hist)


def load_history_tail() -> pd.DataFrame | None:
    hist=load_csv(HISTORY_PATH)
    return hist if hist is not None and not hist.empty else None


def load_rank_matrix() -> dict | None:
    """排名矩阵：计算层从归档现算（None=归档不可读，绝不伪造空表）。"""
    hist = load_history_tail()
    if hist is None:
        return None
    return _rank_matrix(hist)


@st.cache_data(ttl=600,show_spinner=False)
def _rank_matrix(hist):
    return sr.build_rank_matrix(hist)


def _fmt_pct(v) -> str:
    """涨跌幅展示：缺失一律 —，绝不补 0。"""
    try:
        return "—" if v is None or pd.isna(v) else f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def render_top_chart(df_top: pd.DataFrame) -> None:
    """Top N 窗口涨幅条形图（红涨绿跌，缺失不画）。"""
    d = df_top.dropna(subset=["pct_10d"]).copy()
    if d.empty:
        return
    d = d.iloc[::-1]  # 横向条形图自下而上，反转让第 1 名在最上
    d["涨跌色"] = d["pct_10d"].map(lambda x: "#d94f43" if x >= 0 else "#3d9970")
    fig = px.bar(
        d, x="pct_10d", y="name", orientation="h", color="涨跌色",
        color_discrete_map="identity", text="pct_10d",
        labels={"pct_10d": "10日涨幅(%)", "name": ""},
        height=max(320, 34 * len(d)),
    )
    fig.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
    fig.update_layout(
        xaxis=dict(title="10日涨幅(%)", gridcolor="rgba(128,128,128,0.2)"),
        yaxis=dict(categoryorder="array", categoryarray=d["name"].tolist()),
        margin=dict(l=0, r=30, t=10, b=0),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_rank_matrix(matrix: dict | None) -> None:
    """排名矩阵：行=名次（1/2/3 加粗），列=交易日（最新在最左）。

    每格 = 该日该名次的板块名 + 其当日涨跌幅（红涨绿跌，缺失 —）。
    HTML 表格走 ui_compat.html_embed（st.iframe data-URI，跨版本安全）。
    """
    st.subheader("🗓️ 每日 Top10 排名矩阵")
    st.caption("每列一个交易日（最新在最左）；格内 = 该日按 10 日涨幅排到该名次的板块，及其当日涨跌幅。")
    if not matrix or matrix.get("status") != "ok":
        st.caption("排名矩阵暂不可用（历史归档尚未生成），不影响上方榜单。")
        return

    dates = matrix.get("dates") or []
    rows = matrix.get("rows") or []
    if not dates or not rows:
        st.caption("排名矩阵暂无数据。")
        return
    if len(dates) < 10:
        st.caption(f"历史归档已积累 {len(dates)}/10 个交易日，矩阵随每日收盘跑批逐日补齐。")

    head = "".join(f"<th>{d[5:].replace('-', '/')}</th>" for d in dates)
    body_rows = []
    for row in rows:
        rank = row.get("rank")
        rank_cls = ' class="hot"' if isinstance(rank, int) and rank <= 3 else ""
        cells_html = []
        for cell in row.get("cells") or []:
            if not cell:
                cells_html.append('<td><div class="nm">—</div><div class="pct">—</div></td>')
                continue
            pct = cell.get("pct_chg")
            if pct is None:
                pct_html = '<div class="pct">—</div>'
            else:
                cls = "up" if pct >= 0 else "down"
                pct_html = f'<div class="pct {cls}">{pct:+.2f}%</div>'
            cells_html.append(
                f'<td><div class="nm">{_html.escape(str(cell.get("name", "")))}</div>{pct_html}</td>'
            )
        body_rows.append(f'<tr><td{rank_cls}>{rank}</td>{"".join(cells_html)}</tr>')

    css = (
        ".srm{border-collapse:collapse;font-size:12px;color:#1f2328;margin:0 auto}"
        ".srm th,.srm td{border:1px solid #e6e8eb;padding:4px 6px;text-align:center;vertical-align:middle}"
        ".srm th{background:#f6f8fa;font-weight:600;white-space:nowrap}"
        ".srm td:first-child{width:34px;color:#57606a}"
        ".srm td:first-child.hot{font-weight:700;color:#1f2328}"
        ".srm .nm{line-height:1.3;max-width:110px;margin:0 auto}"
        ".srm .pct{line-height:1.3;font-weight:600}"
        ".srm .up{color:#d94f43}"
        ".srm .down{color:#3d9970}"
    )
    table = (
        f'<table class="srm"><thead><tr><th>#</th>{head}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table>'
    )
    html_embed(f"<style>{css}</style>{table}", width=1000, height=560)


def render_concept_comparison() -> None:
    st.subheader("概念指数比较（东财）")
    st.caption("比较概念指数的近10日表现；当日交易题材请看同花顺涨停归因视图。")

    data = load_analysis()

    if not data:
        st.warning("板块轮动数据尚未生成，等待收盘跑批完成。")
        st.caption(_SOURCE_NOTE)
        return

    if data.get("status") == "no_data":
        st.error(f"❌ 板块轮动数据缺失：{data.get('reason', '未知原因')}。请勿重复手动触发跑批，等待下一交易日自动更新。")
        st.caption(_SOURCE_NOTE)
        return

    # ---- 状态条 ----
    note = data.get("note")
    if note:
        st.info(f"ℹ️ {note}")

    if data.get("overlap_count") is not None:
        st.info(f"近10日涨幅榜与{data.get('prev_label') or '上次榜单'}重合 "
                f"{data['overlap_count']}/{data.get('overlap_denominator') or 10} 个。")
    else:
        st.info("缺少相邻交易日归档，因此不计算本次榜单重合数。")

    # 归档缺日期时把「缺了多少」显式说出来：读者据此判断这份判读能不能用来
    # 对比上一交易日，而不是只看一个笼统的「数据日期」。
    if data.get("prev_is_adjacent") is False:
        _gap = data.get("prev_gap_days")
        _gap_txt = (f"相隔 {_gap} 天" if _gap is not None
                    else "日期无法解析、无法判断相隔多久")
        st.caption(
            f"📎 归档在此前缺了交易日（上一份数据 {data.get('prev_date')}，{_gap_txt}，"
            f"已超过相邻交易日判据 {sr.MAX_PREV_GAP_DAYS} 天），因此**不计算榜单重合度**、"
            f"跨空档的重合度反映的是这段空档的累计变化，"
            f"不是日间轮动。"
        )

    # ---- KPI ----
    overlap = data.get("overlap_count")
    denom = data.get("overlap_denominator") or 10
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("数据日期", str(data.get("date", "—")))
    c2.metric(
        "Top10 榜单重合度",
        "—" if overlap is None else f"{overlap}/{denom}",
        help=("与上一交易日近10日涨幅 Top10 的重合数，不代表当日涨幅或资金流向"
              if data.get("prev_is_adjacent") is not False
              else "归档不连续，未计算重合度（跨空档的比较没有「日间轮动」含义）"),
    )
    c3.metric(
        "Top10 10日涨幅中位数",
        "—" if data.get("top_median_pct") is None else f"{data['top_median_pct']:+.1f}%",
    )
    c4.metric(
        "参与概念10日涨幅中位数",
        "—" if data.get("universe_median_pct") is None else f"{data['universe_median_pct']:+.1f}%",
        help=f"同日全部有效概念板块 10日涨幅中位数（共 {data.get('universe_count', '—')} 个）",
    )

    # ---- 明细表 ----
    rows = data.get("top") or []
    if not rows:
        st.info("今日无上榜板块数据。")
        return

    st.subheader(f"🏆 近 10 个交易日涨幅 Top {data.get('top_n', 10)}")
    df_top = pd.DataFrame(rows)
    show = pd.DataFrame({
        "排名": df_top["rank"],
        "板块": df_top["name"],
        "10日涨幅": df_top["pct_10d"].map(_fmt_pct),
        "5日": df_top["pct_5d"].map(_fmt_pct),
        "20日": df_top["pct_20d"].map(_fmt_pct),
        "60日": df_top["pct_60d"].map(_fmt_pct),
        "数据日涨幅": df_top["pct_chg"].map(_fmt_pct),
        "连续在榜": df_top["streak"].map(lambda x: f"{x} 天" if x else "—"),
    })

    def _col_style(col: pd.Series):
        """10日涨幅列：红涨绿跌（缺失无样式）。"""
        def _one(v):
            try:
                x = float(str(v).rstrip("%"))
            except (ValueError, TypeError):
                return ""
            return "color: #d94f43; font-weight: bold" if x >= 0 else "color: #3d9970"
        return [_one(v) for v in col]

    st.dataframe(
        show.style.apply(_col_style, subset=["10日涨幅"]),
        use_container_width=True, hide_index=True, height=420,
    )

    render_top_chart(df_top)

    render_rank_matrix(load_rank_matrix())

    with st.expander("📖 口径与判读方法（点击展开）"):
        st.markdown(f"""
- **榜单**：按 **10 日涨幅**（自然窗口涨幅，非累加日涨幅）对全部概念板块降序取前 {data.get('top_n', 10)}。
  同页附 5/20/60 日与当日涨幅，用于判断「刚启动（5日强、20日弱）」还是「趋势延续（各窗口同强）」。
- **榜单重合度**：Top10 与上一交易日有多少相同成员。10日窗口本身大量重叠，
  成员稳定不等于当日继续领涨；应同时核对当日涨幅和题材内部的上涨家数。
- **连续在榜**：该板块连续多少个交易日停留在 Top10，断一天就重新从 1 计。
- **排名矩阵**：行=名次 1~10（第 1/2/3 名加粗），列=交易日（最新在最左）；
  格内是「该日按 10 日涨幅排到该名次」的板块及其**当日涨跌幅**。
  同一名次跨日看是「谁在霸榜」，同一板块换列看是「名次进退」；
  归档积累中（不足 10 天时如实显示已积累天数），缺失格子显示 —，不拿别日顶替。
- **已剔除**：昨日涨停、基金重仓、沪深股通这类属性型/复盘型板块——它们不是题材，混进榜里会污染答案。
- **数据源**：{_SOURCE_NOTE}
- **判读是客观描述，不构成任何操作建议**；不提供买点、目标价或仓位指引。
""")


def render_sector_rotation_page() -> None:
    st.header("🔄 板块轮动")
    view=st.radio("观察口径",["当日交易题材（同花顺归因）","概念指数比较（东财）"],horizontal=True,key="sector_view")
    if view=="当日交易题材（同花顺归因）":
        render_activity(key="rotation_theme")
    else:
        render_concept_comparison()

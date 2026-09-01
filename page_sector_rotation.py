"""板块轮动展示页：近 10 个交易日涨幅 Top10 概念板块 + 主升/轮动判读。

数据链路：daily_sector_rotation.py（跑批）→ data/sector_rotation/{history.csv,
analysis.json} → 本页只做渲染，不做计算（计算在 sector_rotation.py 计算层）。

口径说明（必须如实展示）：
- 板块口径是「东财概念板块」（约 500 个）。用户语境里的「同花顺题材概念」
  没有免费的全量日频接口（名称接口无涨跌幅、历史接口逐板块限流），
  东财概念板块覆盖等价的题材集合且一次请求组拿全量多窗口涨幅。
- 属性型/复盘型板块（昨日涨停、基金重仓、沪深股通…）已从题材榜排除。
- 判读是启发式描述（榜单重合度），不是操作建议。
"""
from __future__ import annotations

import json
import os

import pandas as pd
import plotly.express as px
import streamlit as st

ANALYSIS_PATH = "data/sector_rotation/analysis.json"
HISTORY_PATH = "data/sector_rotation/history.csv"

_SOURCE_NOTE = (
    "口径：东财概念板块（约 500 个，属性型板块已剔除）。"
    "「同花顺题材」无免费全量日频源，故用等价的东财概念口径。"
    "判读依据榜单重合度（重合≥7 主升 / ≤3 轮动），是描述不是建议。"
)


@st.cache_data(ttl=600)
def load_analysis() -> dict | None:
    if not os.path.exists(ANALYSIS_PATH):
        return None
    try:
        with open(ANALYSIS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@st.cache_data(ttl=600)
def load_history_tail() -> pd.DataFrame | None:
    if not os.path.exists(HISTORY_PATH):
        return None
    try:
        return pd.read_csv(HISTORY_PATH, dtype={"code": str})
    except Exception:
        return None


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


def render_sector_rotation_page() -> None:
    st.header("🔄 板块轮动")
    st.caption("近 10 个交易日涨幅最高的题材概念板块 + 主升/轮动判读 | 每日收盘后更新")

    data = load_analysis()

    if not data:
        st.warning("📊 板块轮动数据初始化中，请等待今日收盘跑批完成（约 19:40 后）。")
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

    verdict = data.get("verdict") or {}
    label = verdict.get("label", "—")
    if label == "主线主升":
        st.success(f"📌 周期判读：**{label}**")
    elif label == "快速轮动":
        st.warning(f"📌 周期判读：**{label}**")
    elif label == "样本不足":
        st.info(f"📌 周期判读：**{label}**")
    else:
        st.info(f"📌 周期判读：**{label}**")

    for reason in verdict.get("reasons") or []:
        st.markdown(f"- {reason}")
    if verdict.get("watch"):
        st.caption(f"🔭 明日观察：{verdict['watch']}")

    # ---- KPI ----
    overlap = data.get("overlap_count")
    denom = data.get("overlap_denominator") or 10
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("数据日期", str(data.get("date", "—")))
    c2.metric(
        "Top10 榜单重合度",
        "—" if overlap is None else f"{overlap}/{denom}",
        help="与上一交易日 Top10 的重合数：≥7 主升，≤3 轮动",
    )
    c3.metric(
        "Top10 10日涨幅中位数",
        "—" if data.get("top_median_pct") is None else f"{data['top_median_pct']:+.1f}%",
    )
    c4.metric(
        "全市场概念中位数",
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
        "今日": df_top["pct_chg"].map(_fmt_pct),
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

    with st.expander("📖 口径与判读方法（点击展开）"):
        st.markdown(f"""
- **榜单**：按 **10 日涨幅**（自然窗口涨幅，非累加日涨幅）对全部概念板块降序取前 {data.get('top_n', 10)}。
  同页附 5/20/60 日与当日涨幅，用于判断「刚启动（5日强、20日弱）」还是「趋势延续（各窗口同强）」。
- **主升 vs 轮动**：看 Top10 与上一交易日的**重合度**——重合 ≥7 视为主线主升（资金持续聚焦同一批板块），
  ≤3 视为快速轮动（领涨板块天天换脸），中间为过渡/混合。阈值是经验约定，页面上理由都带原始数字，可自行对账。
- **连续在榜**：该板块连续多少个交易日停留在 Top10，断一天就重新从 1 计。
- **已剔除**：昨日涨停、基金重仓、沪深股通这类属性型/复盘型板块——它们不是题材，混进榜里会污染答案。
- **数据源**：{_SOURCE_NOTE}
- **判读是客观描述，不构成任何操作建议**；不提供买点、目标价或仓位指引。
""")

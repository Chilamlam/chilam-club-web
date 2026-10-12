# -*- coding: utf-8 -*-
"""行业信号展示页：申万二级行业「低市值占比 + 高资金热度」筛选结果。

数据链路：daily_industry_signal.py（跑批）→ data/industry_signal/{signal.json,
history.csv} → 本页只装配与渲染，**不做任何计算**（分位、命中、排序全在
industry_signal.py 计算层，页面不许自己重写一遍不等式）。

门禁设计（沿用全站原则：锁内容会把人赶走，锁投递才可持续）
----------------------------------------------------------
本页内容**开放**。页面上每个数字都能被同一天任何公开行情源复算出来，锁它没有
意义；而且它是「收盘后自动送到你手上」这个付费权益最好的样本——看不懂的人
不会订阅。真正付费的是**投递**（页尾的订阅引导），与 page_digest 一致。

口径必须如实展示（写死在页面里的阈值一律不准，全部读 signal.json）
------------------------------------------------------------------
- 行业口径是申万 2021 版二级行业（共 134 个，其中**未发布**的约 10 个没有独立
  指数行情，已被剔除）。不剔除的话名单里会凭空多出几个永远不命中的空位。
- 市值占比 = 该行业流通市值 ÷ 当日全部参与行业流通市值之和（百分数）。
  分母同源是刻意的：分子分母来自同一份归档，任何人可逐日复算，
  避免「外部全A总市值当分母」带来的口径漂移。
- 两个分位都是**含当日**的中位秩分位（状态量：今天处在什么位置），
  不是用历史算出来的判据阈值——两者语义不同，不要混。
- 命中三态必须分开说：①有命中 ②今日确实没有命中（各行业已逐一核对）
  ③数据不可用/样本不足。②③绝不能都渲染成一张空表或一句「暂无数据」——
  那会让读者把「今天真的没有」读成「这一段坏了」，反之亦然。
- 命中高亮用**橙金**而不是红色：红色在本站是「涨」，绿色是「跌」，
  拿红点表示命中会被直接误读成涨跌信号。图例里也写清楚了。
"""
from __future__ import annotations

import json
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import auth
import data_freshness as freshness
import industry_signal as isig

SIGNAL_PATH = os.path.join("data", "industry_signal", "signal.json")
HISTORY_PATH = os.path.join("data", "industry_signal", "history.csv")

# widget key：必须显式给且全局唯一——同进程里两个页面用同名 key 会抛
# StreamlitDuplicateElementKey，而单跑任一页都不暴露。
TREND_KEY = "isig_trend_code"
HITONLY_KEY = "isig_hit_only"

UP_COLOR = "#d94f43"    # 涨：红（A 股习惯，与本项目其余页面一致）
DOWN_COLOR = "#3d9970"  # 跌：绿
HIT_COLOR = "#f59e0b"   # 命中高亮：橙金，刻意避开红/绿，免得被读成涨跌
HIT_EDGE = "#b45309"
DIM_COLOR = "#c3c9d1"
GRID_COLOR = "rgba(128,128,128,0.2)"

_SOURCE_NOTE = (
    "数据源：tushare 申万行业指数日线（自带流通市值与成交额，历史可回溯），"
    "每日收盘后跑批写入归档。含当日的中位秩分位：并列值各分一半，"
    "所以分位最小值不是 0 而是 0.5/N、最大值是 (N-0.5)/N。"
)


@st.cache_data(ttl=600)
def load_signal() -> dict | None:
    """读每日结论。文件缺失/损坏返回 None（取数失败 ≠ 空数据）。"""
    if not os.path.exists(SIGNAL_PATH):
        return None
    try:
        with open(SIGNAL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@st.cache_data(ttl=600)
def load_history() -> pd.DataFrame | None:
    return isig.load_history(HISTORY_PATH)


def _fmt(v, suffix: str = "", nd: int = 2) -> str:
    """数值展示：缺失一律 —，绝不补 0（补 0 会被读成"占比确实是 0"）。"""
    try:
        if v is None or pd.isna(v):
            return "—"
        return f"{float(v):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _fmt_chg(v) -> str:
    try:
        if v is None or pd.isna(v):
            return "—"
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _chg_color(v) -> str:
    s = _fmt_chg(v)
    if s == "—":
        return ""
    return f"color: {UP_COLOR}; font-weight: 600" if float(v) >= 0 else f"color: {DOWN_COLOR}"


def _hover_block(rec: dict) -> list:
    """散点图 hover 的自定义数据（顺序与 hovertemplate 的 customdata[i] 对应）。"""
    l1 = rec.get("l1_name")
    return [
        str(rec.get("name") or rec.get("code") or "—"),
        "—" if l1 is None or pd.isna(l1) else str(l1),
        _fmt(rec.get("mv_share"), "%"),
        _fmt(rec.get("amt_share"), "%"),
        _fmt_chg(rec.get("pct_chg")),
    ]


_HOVER = (
    "<b>%{customdata[0]}</b>（%{customdata[1]}）<br>"
    "市值占比 %{customdata[2]}<br>"
    "成交额占比 %{customdata[3]}<br>"
    "当日 %{customdata[4]}<extra></extra>"
)


def render_freshness(sig: dict, reference_date: str = "") -> None:
    """数据日期与过期提示。放在所有内容之前——过期一天的图看着与正常图一样。

    reference_date 由调用方（app.py 的 site_reference_date）传入而不是本页
    自己再算一遍：一份规则两处实现必然漂移，哪天基准口径改了只会改到一处。
    """
    v = freshness.verdict(sig.get("date"), reference_date=reference_date or None)
    shown = f"{v['date'][:4]}-{v['date'][4:6]}-{v['date'][6:]}" if v["date"] else "未知"
    if v["status"] == "stale":
        st.error(
            f"⚠️ **行业信号数据可能未更新：{v['reason']}。** 请按 {shown} 这个日期"
            f"理解页面上所有数字——它不是最新交易日的结果。若持续多日如此，"
            f"说明跑批该步骤失败，需要人工补跑。",
            icon="🚨",
        )
    elif v["status"] == "unknown":
        st.warning("⚠️ 行业信号产物里找不到日期戳，无法判断是否为最新，请谨慎使用。")
    else:
        st.caption(f"📅 数据日期：**{shown}**　|　归档 **{sig.get('trade_days_available', '—')}** "
                   f"个交易日（自 {sig.get('first_date', '—')} 起）")


def render_quadrant(frame: pd.DataFrame, th: dict) -> None:
    """四象限散点图：x=市值占比分位，y=成交额占比分位，左上角为命中区。

    阈值、区域边界、标注数字全部取自 signal.json 的 thresholds——
    页面上不许出现写死的 30/80，否则哪天改了阈值，图还是按老阈值画，
    而且看起来完全正常。
    """
    mv_max = th.get("mv_pct_max")
    amt_min = th.get("amt_pct_min")
    if mv_max is None or amt_min is None:
        st.caption("结论里没有阈值字段，散点图暂不绘制。")
        return
    mv_max, amt_min = float(mv_max), float(amt_min)

    d = frame.dropna(subset=["mv_pct", "amt_pct"])
    if d.empty:
        st.caption("没有任何行业同时具备两个分位，散点图暂不绘制。")
        return
    hit = d[d["is_hit"]]
    rest = d[~d["is_hit"]]

    fig = go.Figure()
    # 命中区底色（先画，压在最下层）
    fig.add_shape(type="rect", x0=0, x1=mv_max, y0=amt_min, y1=100,
                  fillcolor="rgba(245,158,11,0.12)", line=dict(width=0), layer="below")
    for x in (mv_max,):
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=100,
                      line=dict(color=HIT_COLOR, width=1, dash="dash"))
    for y in (amt_min,):
        fig.add_shape(type="line", x0=0, x1=100, y0=y, y1=y,
                      line=dict(color=HIT_COLOR, width=1, dash="dash"))

    if not rest.empty:
        fig.add_trace(go.Scatter(
            x=rest["mv_pct"], y=rest["amt_pct"], mode="markers", name="其他行业",
            marker=dict(size=7, color=DIM_COLOR),
            customdata=[_hover_block(r) for r in rest.to_dict("records")],
            hovertemplate=_HOVER,
        ))
    if not hit.empty:
        fig.add_trace(go.Scatter(
            x=hit["mv_pct"], y=hit["amt_pct"], mode="markers+text", name="命中",
            marker=dict(size=14, color=HIT_COLOR, line=dict(color=HIT_EDGE, width=1.2)),
            text=[str(r.get("name") or "—") for r in hit.to_dict("records")],
            textposition="top center", textfont=dict(size=11, color=HIT_EDGE),
            customdata=[_hover_block(r) for r in hit.to_dict("records")],
            hovertemplate=_HOVER,
        ))

    fig.add_annotation(
        x=mv_max, y=100, xanchor="right", yanchor="top", showarrow=False,
        align="left", font=dict(size=11, color=HIT_EDGE),
        text=f"命中区：市值占比分位 &lt; {mv_max:g}<br>且 热度分位 &gt; {amt_min:g}",
    )
    fig.update_xaxes(title="市值占比分位（0=自身历史最低，100=最高）",
                     range=[-3, 103], gridcolor=GRID_COLOR, zeroline=False)
    fig.update_yaxes(title="成交额占比分位（0=自身历史最冷，100=最热）",
                     range=[-3, 103], gridcolor=GRID_COLOR, zeroline=False)
    fig.update_layout(
        height=520, margin=dict(l=0, r=10, t=10, b=0),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        hovermode="closest",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"左上角橙色区域 = 命中区。**橙点是为高亮，与红涨绿跌无关**——"
        f"红色在本站表示上涨。共 {len(d)} 个行业，命中 {len(hit)} 个。"
    )


def render_picks(picks: list, th: dict, mv_w, amt_w, industry_count) -> None:
    """今日命中卡片。0 命中也是有效结论，必须明说而不是留空。

    刻意不用 st.metric 的 delta 显示涨跌：Streamlit 的 delta 默认
    「正数=绿色」，与 A 股红涨绿跌正好相反，会直接把涨读成跌。
    涨跌一律用带颜色的文字（_chg_color）呈现。
    """
    if not picks:
        st.info(
            f"📭 **今日无同时满足两条件的行业**（全 {industry_count or '—'} 个行业已逐一核对）。"
            "这是有效结论，不是数据缺失——两个条件同时成立本身并不常见。"
        )
        return
    for p in picks:
        l1 = p.get("l1_name") or "—"
        color = _chg_color(p.get("pct_chg")) or "color: #57606a"
        st.markdown(
            f"**{p.get('name', '—')}**　<span style='color:#57606a'>（{l1}）</span>　"
            f"<span style='{color}'>{_fmt_chg(p.get('pct_chg'))}</span>",
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        c1.metric(f"市值占比 · {mv_w} 日分位 {_fmt(p.get('mv_pct'), '', 1)}",
                  _fmt(p.get("mv_share"), "%"))
        c2.metric(f"成交额占比 · {amt_w} 日分位 {_fmt(p.get('amt_pct'), '', 1)}",
                  _fmt(p.get("amt_share"), "%"))
        c3.metric("当日成交额 / 流通市值",
                  f"{_fmt(p.get('amount_yi'), ' 亿')} / {_fmt(p.get('float_mv_yi'), ' 亿')}")


def render_table(frame: pd.DataFrame, th: dict, hit_only: bool) -> None:
    d = frame[frame["is_hit"]] if hit_only else frame
    if d.empty:
        st.caption("筛选后没有行业。")
        return
    show = pd.DataFrame({
        "行业": d["name"],
        "一级行业": d["l1_name"].fillna("—"),
        "市值占比": d["mv_share"].map(lambda v: _fmt(v, "%")),
        "市值分位": d["mv_pct"].map(lambda v: _fmt(v, "", 1)),
        "成交额占比": d["amt_share"].map(lambda v: _fmt(v, "%")),
        "热度分位": d["amt_pct"].map(lambda v: _fmt(v, "", 1)),
        "当日涨跌": d["pct_chg"].map(_fmt_chg),
        "当日成交额": d["amount_yi"].map(lambda v: _fmt(v, " 亿")),
        "流通市值": d["float_mv_yi"].map(lambda v: _fmt(v, " 亿")),
    })
    chg = list(d["pct_chg"])
    st.dataframe(
        show.style.apply(lambda col: [_chg_color(v) for v in chg], subset=["当日涨跌"]),
        use_container_width=True, hide_index=True, height=460,
    )
    st.caption(
        f"表内 {len(d)} 行，按「热度分位降序 → 市值分位升序」排列"
        f"（保证每次渲染顺序确定，不因并列而变）。"
        f"阈值：市值分位低于 {float(th.get('mv_pct_max', 0)):g} 且 "
        f"热度分位高于 {float(th.get('amt_pct_min', 0)):g} —— 命中的行会出现在最前。"
    )


def render_trend(code: str, label: str, th: dict) -> None:
    """单行业占比与分位走势。分位逐点只用截至该点的窗口（无前视偏差）。"""
    hist = load_history()
    if hist is None:
        st.caption("历史归档不可读，走势暂不可用（不影响上方当日结论）。")
        return
    ser = isig.industry_series(hist, code)
    if ser is None or ser.empty:
        st.caption(f"归档里没有 {label} 的历史序列，走势暂不可用。")
        return

    mv_w = int(ser["mv_window_len"].max()) if "mv_window_len" in ser.columns else None
    amt_w = int(ser["amt_window_len"].max()) if "amt_window_len" in ser.columns else None
    first_mv = int(ser["mv_window_len"].iloc[0]) if "mv_window_len" in ser.columns else None

    st.caption(
        f"共 {len(ser)} 个交易日（{ser['date'].iloc[0]} ~ {ser['date'].iloc[-1]}）。"
        f"分位逐点只用「截至该交易日」的窗口计算，不含未来数据。"
        + (f" 最早那个点的市值窗口只有 {first_mv} 日（归档起点限制），"
           f"最新的点为 {mv_w} 日。" if first_mv is not None and mv_w is not None
           and first_mv < mv_w else "")
    )

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ser["date"], y=ser["mv_share"], mode="lines",
                             name="市值占比(%)",
                             line=dict(color="#3498db", width=2)))
    fig.add_trace(go.Scatter(x=ser["date"], y=ser["amt_share"], mode="lines",
                             name="成交额占比(%)",
                             line=dict(color=HIT_COLOR, width=2)))
    fig.update_layout(height=300, margin=dict(l=0, r=10, t=10, b=0),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0))
    fig.update_xaxes(gridcolor=GRID_COLOR)
    fig.update_yaxes(title="占全市场比重(%)", gridcolor=GRID_COLOR)
    st.plotly_chart(fig, use_container_width=True)

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=ser["date"], y=ser["mv_pct"], mode="lines",
                              name="市值占比分位", line=dict(color="#3498db", width=2)))
    fig2.add_trace(go.Scatter(x=ser["date"], y=ser["amt_pct"], mode="lines",
                              name="热度分位", line=dict(color=HIT_COLOR, width=2)))
    mv_max = th.get("mv_pct_max")
    amt_min = th.get("amt_pct_min")
    if mv_max is not None:
        fig2.add_hline(y=float(mv_max), line=dict(color="#3498db", width=1, dash="dot"),
                       annotation_text=f"市值分位阈值 {float(mv_max):g}")
    if amt_min is not None:
        fig2.add_hline(y=float(amt_min), line=dict(color=HIT_COLOR, width=1, dash="dot"),
                       annotation_text=f"热度阈值 {float(amt_min):g}")
    fig2.update_layout(height=300, margin=dict(l=0, r=10, t=10, b=0),
                       plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                       legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0))
    fig2.update_xaxes(gridcolor=GRID_COLOR)
    fig2.update_yaxes(title="分位(0~100)", range=[-3, 103], gridcolor=GRID_COLOR)
    st.plotly_chart(fig2, use_container_width=True)
    st.caption("两条曲线交叉并满足阈值（蓝线在阈值线下方、橙线在阈值线上方）时才构成命中。")


def _render_subscribe() -> None:
    """页尾订阅引导：内容免费，付费的是投递——与 page_digest 同一套逻辑。"""
    st.markdown("---")
    st.markdown("### 📮 想在收盘后自动收到？")
    st.caption(
        "本页内容始终免费。付费部分是**投递**：每个交易日收盘后（约 19:40）"
        "自动推送到你的微信，这一段会出现在当日摘要里，还带「你的池子今日」"
        "这一段用你自己自选股算的内容。"
    )
    if not auth.is_logged_in():
        st.info("🔐 登录后可开启推送订阅。")
        if st.button("前往登录 / 注册", key="isig_login", use_container_width=False):
            st.switch_page("pages/auth.py")
        return
    if not auth.is_vip():
        st.warning("👑 推送订阅为 VIP 权益，开通后每日收盘自动送达微信。")
        if st.button("👑 查看开通方式", key="isig_vip", use_container_width=False):
            st.switch_page("pages/dashboard.py")
        return
    days = auth.get_vip_remaining_days()
    st.success(
        f"✅ 推送权益已生效（VIP 剩余 {days} 天）。" if days is not None
        else "✅ 推送权益已生效。"
    )


def render_industry_signal_page(reference_date: str = "") -> None:
    st.header("🏭 行业信号")
    st.caption("申万二级行业｜低市值占比（相对自身历史缩水）+ 高资金热度（成交占比重回高位）｜每日收盘后更新")

    sig = load_signal()
    if not sig:
        st.warning("📊 行业信号数据初始化中，请等待今日收盘跑批完成（约 19:40 后）。")
        st.caption(_SOURCE_NOTE)
        return

    status = sig.get("status")
    if status == "no_data":
        st.error(
            f"❌ 行业信号数据缺失：{sig.get('reason', '未知原因')}。"
            "请勿重复手动触发跑批，等待下一交易日自动更新。"
        )
        st.caption(_SOURCE_NOTE)
        return
    if status == "insufficient":
        # 与 no_data 分开报：样本不足是「归档积累中」，数据缺失是「这一步坏了」，
        # 处置方式完全不同，合并成一个提示会让人去修错的东西。
        st.info(
            f"⏳ 归档还在积累：{sig.get('reason', '样本不足')}。"
            f"在样本足够之前不给结论——给一个基于十几个交易日的分位结论，"
            f"看起来和真结论一模一样，但毫无意义。"
        )
        st.caption(_SOURCE_NOTE)
        return

    render_freshness(sig, reference_date)

    th = sig.get("thresholds") or {}
    mv_w = sig.get("mv_window_actual") or sig.get("mv_window")
    amt_w = sig.get("amt_window_actual") or sig.get("amt_window")
    frame = isig.universe_frame(sig)

    # ---- KPI ----
    hits = sig.get("hits_count", 0)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("今日命中", f"{hits} 个", help="同时满足两个条件的行业数（含未列入下方的）")
    c2.metric("参与行业", f"{sig.get('industry_count', '—')} 个", help="申万二级行业中已发布的那些")
    tot_mv = sig.get("universe_mv_total_yi")
    c3.metric("全市场流通市值",
              _fmt(None if tot_mv is None else float(tot_mv) / 10000.0, " 万亿"),
              help="当日参与行业流通市值合计（占比的分母）")
    tot_amt = sig.get("universe_amt_total_yi")
    c4.metric("全市场成交额", _fmt(tot_amt, " 亿"), help="当日参与行业成交额合计")

    # ---- 命中卡片 ----
    # 刻意放在 frame 判空之前：命中名单只依赖 sig["picks"]，与明细表是否能读出来
    # 无关。之前把它关在 else 分支里，明细缺失时警告文案写着「只有下方的命中名单
    # 可用」，而下方的命中名单其实根本没渲染出来——文案与行为不一致。
    st.subheader("🎯 今日命中")
    picks = sig.get("picks") or []
    shown = len(picks)
    if hits > shown:
        st.caption(
            f"下方按热度分位列出前 {shown} 个；另有 {hits - shown} 个同样满足条件，"
            f"完整名单见页面下方的明细表。"
        )
    render_picks(picks, th, mv_w, amt_w, sig.get("industry_count"))

    if frame is None:
        st.warning(
            "结论里没有行业明细（可能产物版本较旧）：散点图、明细表与走势暂不可用，"
            "上面的命中名单不受影响。"
        )
    else:
        # ---- 散点图 ----
        st.subheader("📈 全行业分位分布")
        render_quadrant(frame, th)

        # ---- 明细表 ----
        st.subheader("📋 全行业分位明细")
        hit_only = st.checkbox("只看命中的行业", value=False, key=HITONLY_KEY)
        render_table(frame, th, hit_only)

        # ---- 走势 ----
        st.subheader("🔍 单行业走势")
        all_codes = [c for c in frame["code"].astype(str).tolist() if c and c != "nan"]
        # 命中的排在最前：下拉框默认选中第一个命中的行业（也就是本页最想让你看的那个）。
        # 注意不能直接用 frame 的行序当「命中优先」——frame 是按热度分位降序排的，
        # 一个**未命中**但热度分位最高的行业会排在所有命中行业前面，默认就落在它身上。
        hit_codes = [c for c in frame.loc[frame["is_hit"], "code"].astype(str).tolist()
                     if c and c != "nan"]
        codes = hit_codes + [c for c in all_codes if c not in hit_codes]
        if not codes:
            st.caption("没有可用于走势的行业。")
        else:
            labels = {
                str(r["code"]): f"{r['name']}（{r['l1_name'] if pd.notna(r['l1_name']) else '—'}）"
                for _, r in frame.iterrows()
            }
            # 用 session_state 预置而不是 value=：带 key 的 widget 在 rerun 时
            # 只认自己存下的旧值、会忽略 value=，所以必须走 session_state。
            # 同时兼作「跨日陈旧值兜底」：昨天选的行业今天可能不在名单里，
            # 直接留着会渲染出空图或抛错。
            if st.session_state.get(TREND_KEY) not in codes:
                st.session_state[TREND_KEY] = codes[0]
            code = st.selectbox("选择行业（查看占比与分位走势）", codes,
                                format_func=lambda c: labels.get(str(c), str(c)),
                                key=TREND_KEY)
            render_trend(str(code), labels.get(str(code), str(code)), th)

    with st.expander("📖 口径与判读方法（点击展开）"):
        st.markdown(f"""
- **在找什么**：同一天同时满足两件事的行业——
  ①**市值占比处在自己的历史低位**（相对整个市场缩水了）；
  ②**成交额占比处在自己的历史高位**（资金关注度骤升）。
  组合含义是「跌够了 + 刚开始有人来」，即低位启动候选。**这是描述性筛选，不是买卖建议。**
- **市值占比** = 该行业流通市值 ÷ 当日全部参与行业流通市值之和。
  分母用「同日同口径全体行业之和」而不是外部的全 A 总市值：分子分母同源，
  任何人可以拿归档逐日复算，不存在第二个来源造成的口径漂移。
- **两个窗口不一样长是刻意的**：
  - 市值占比分位取近 **{sig.get('mv_window')}** 个交易日 —— 占比是慢变量，
    看短期会被噪声主导，必须用长窗口才能表达「相对自身历史缩水」。
  - 成交额占比分位取近 **{sig.get('amt_window')}** 个交易日 —— 资金关注度是快变量，
    {sig.get('amt_window')} 日才能体现出「最近正在挤进来」而不是「一直很热」。
- **分位是含当日的中位秩分位**：当日值在窗口里的位置（0=最小，100=最大）。
  用中位秩是为了并列值各分一半——否则一串相同的值会被整体判成高分位。
  样本为空或值缺失一律显示 —，**绝不用 0 或 50 冒充「算出来了」**。
- **命中条件是严格不等式**：市值分位**低于** {float(th.get('mv_pct_max', 0)):g}
  且 热度分位**高于** {float(th.get('amt_pct_min', 0)):g}。恰好等于阈值不算命中
  （所以别把「分位恰好等于阈值」的行业当成命中了）。
- **为什么每天只推前 {int(th.get('max_picks', 3))} 个**：满足条件的行业数本身有波动，
  全部列出会让推送变长且重点不清；页面上的明细表始终是**完整**的，
  推送里也会写明「另有 N 个满足条件」——不会让你以为只有这几个。
- **截面残缺日整日剔除**：某交易日归档的行业数明显少于常态（接口只返回半份）时，
  整日不参与计算。因为占比的分母是当日全体行业之和，少一半行业会让每个行业的
  占比凭空放大近一倍，而结果看起来仍然是一组正常的百分数，**没有任何下游迹象**。
- **样本不足不出结论**：归档不足 {isig.MIN_TRADE_DAYS} 个完整截面交易日时不给结论。
  分位窗口也不足时如实标注实际长度，不假装窗口是满的。
- **数据源**：{_SOURCE_NOTE}
- **本页只做描述，不提供方向判断、目标价、买卖点或仓位指引。**
  所有数据与名单由程序按固定量化规则自动生成，仅供研究参考，
  **不构成投资建议**；历史统计不代表未来收益，据此操作的风险自担。
""")

    # 页面级免责声明：侧边栏那份（app.py）在有 iframe 壳时会被滚出视野，
    # 页面底部再落一条，保证「只看到这一页」的人也能看到。
    st.caption(
        "⚠️ 本页所有数据与名单均由程序按固定量化规则自动生成，仅供研究参考，"
        "**不构成投资建议**，不提供个股推荐、目标价或买卖点。"
        "行业分类与行情数据来自第三方接口，可能存在延迟、缺失或错误。"
    )

    _render_subscribe()

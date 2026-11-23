# -*- coding: utf-8 -*-
"""
「晚间复盘与次日交易计划」展示页（page_evening_plan.py）

架构定位：
  - 纯展示层：只读取跑批产物 data/evening_plan/latest.json 与 data/methodology_181.json，
    以及调用计算层 evening_plan.py 做纯内存结构映射；绝不直连外部行情 API（无 tushare/akshare/requests/urllib）。
  - 免费层 vs VIP 付费层（遵循全站「锁送达、锁个人视角、锁历史验证，不锁公开客观池」原则）：
      · 免费公开：每日 6 大客观战法选股池、情绪周期与日历特殊法则雷达、次日盘中 5 大时间节点沙盘、181 篇方法论检索库。
      · VIP 专属：
          1) 四大情绪周期 × 战法历史晋级率矩阵（只列实际晋级记录与日期）
          2) 个人自选股 × 六类观察条件与风险标签（并随每晚微信摘要主动推送到手机）
          3) 个人复盘历史对比与 #165「两个数据（胜率与盈亏比）」回验账本
"""
from __future__ import annotations

import json
import os
import re
import hashlib

import pandas as pd
import streamlit as st

import auth
import data_freshness as freshness
import database
import evening_plan as ep
import investor_journey as journey
import push_binding as pb
import review_language


def _fmt_pct(v, digits: int = 2, sign: bool = True) -> str:
    """格式化百分比，缺失一律返回 '—'，绝不补 0。"""
    if v is None:
        return "—"
    try:
        fv = float(v)
        if pd.isna(fv):
            return "—"
        fmt = f"{{:{'+' if sign else ''}.{digits}f}}%"
        return fmt.format(fv)
    except (TypeError, ValueError):
        return "—"


def _fmt_num(v, digits: int = 2, suffix: str = "") -> str:
    """格式化数值，缺失一律返回 '—'，绝不补 0。"""
    if v is None:
        return "—"
    try:
        fv = float(v)
        if pd.isna(fv):
            return "—"
        return f"{fv:.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _xueqiu_link(code: str) -> str:
    c = str(code or "").strip().upper().split(".")[0]
    if not c.isdigit() or len(c) != 6:
        return ""
    if c[:2] in ("92", "43", "83", "87"):
        prefix = "BJ"
    elif c.startswith(("6", "5", "9")):
        prefix = "SH"
    else:
        prefix = "SZ"
    return f"https://xueqiu.com/S/{prefix}{c}"


@st.cache_data(ttl=600, show_spinner=False)
def _load_plan_cached(wording_revision: str) -> dict | None:
    return ep.load_evening_plan()


def _load_plan() -> dict | None:
    source = open(review_language.__file__, "rb").read()
    return _load_plan_cached(hashlib.sha256(source).hexdigest())


@st.cache_data(ttl=600, show_spinner=False)
def _load_kb() -> dict:
    return ep.load_methodology_kb()


@st.cache_data(ttl=600, show_spinner=False)
def _load_csv_cached(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _site_ref_date(plan_date: str) -> str:
    cands = [freshness.norm_date(plan_date)]
    for p, cols in (
        ("data/breakout_stocks.csv", ("update_date",)),
        ("data/strong_stocks.csv", ("更新日期",)),
    ):
        df = _load_csv_cached(p)
        if not df.empty:
            cands.append(freshness.pick_date(df, cols))
    cands = [c for c in cands if c]
    return max(cands) if cands else ""


def _render_freshness_and_disclaimer(plan: dict) -> dict:
    """在页面最顶部渲染数据新鲜度横幅与合规免责声明（先于任何表格与指标）。"""
    p_date = str(plan.get("date") or "")
    v = freshness.verdict(freshness.norm_date(p_date), reference_date=_site_ref_date(p_date))
    shown = f"{v['date'][:4]}-{v['date'][4:6]}-{v['date'][6:]}" if v["date"] else "未知"
    if v["status"] == "stale":
        st.warning(
            f"**数据截至 {shown}** · {v['reason']}。页内标的均属于这个交易日。",
            icon="⚠️",
        )
        with st.expander("查看日期说明"):
            st.write("本页不会把历史日期伪装成最新交易日。若日期持续未推进，需要核对跑批是否完成。")
    elif v["status"] == "unknown":
        st.warning(f"晚间候选池无法确认数据日期：{v['reason']}。", icon="⚠️")
    else:
        st.caption(
            f"📅 数据基准交易日：**{shown}**（生成时间：{plan.get('generated_at', '—')}） ｜ "
            f"ℹ️ {plan.get('disclaimer') or ep.DISCLAIMER}"
        )
    return v


# ================= 区块 1：情绪周期与日历特殊法则雷达 =================

def _render_cycle_and_laws(plan: dict) -> None:
    cycle = plan.get("cycle_and_laws") or {}
    cur_phase = cycle.get("current_phase") or "—"
    weekday = cycle.get("weekday") or "—"
    laws = cycle.get("active_laws") or []
    split_125 = cycle.get("first_board_split_125") or {}
    mid_065 = cycle.get("mid_tier_diffusion_065") or {}
    recent_phases = cycle.get("recent_phases") or []

    st.markdown("### 情绪记录与日历")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("当前情绪周期", f"{cur_phase}", help="按当日连板样本与历史分位阈值得到的统计分组，不预测次日方向")
    c2.metric("日历节点", f"{weekday}", help="记录日期与节后间隔，供逐项核对作者方法中的日历观察条件")

    if split_125.get("status") == "ok":
        pre_b = split_125.get("pre_10am") or {}
        post_b = split_125.get("post_10am") or {}
        pre_r = _fmt_pct((pre_b.get("promo_rate") or 0) * 100 if pre_b.get("promo_rate") is not None else None, 1, sign=False)
        post_r = _fmt_pct((post_b.get("promo_rate") or 0) * 100 if post_b.get("promo_rate") is not None else None, 1, sign=False)
        spread = split_125.get("promo_rate_spread")
        c3.metric(
            "昨日 10:00前首板晋级率",
            f"{pre_r} ({pre_b.get('promoted', 0)}/{pre_b.get('base', 0)})",
            delta=(f"较10点后 {spread*100:+.1f}%" if spread is not None else f"10点后 {post_r}"),
            help="#125 观察条件：对比昨日 10:00 前封板与 10:00 后封板在今日的 1进2 归档结果",
        )
    else:
        c3.metric("昨日 10:00前首板晋级率", "—", help=str(split_125.get("reason") or "缺少昨日首板或今日归档"))

    if mid_065.get("status") in ("ok", "partial"):
        loss_r = mid_065.get("big_loss_rate")
        mid_n = mid_065.get("mid_n", 0)
        if loss_r is not None:
            c4.metric(
                "昨日中位股(3~4板)跌幅≥5%占比",
                _fmt_pct(loss_r * 100, 0, sign=False),
                delta=("达到 #065 阈值" if mid_065.get("warning") else "未达到 #065 阈值"),
                delta_color="inverse" if mid_065.get("warning") else "normal",
                help="昨日 3~4 板样本今日的中位涨跌幅和跌幅≥5%的占比；只描述这组股票，不推断向全市场传导",
            )
        else:
            c4.metric(
                "昨日中位股(3~4板)晋级",
                f"{mid_065.get('promoted_count', 0)} / {mid_n} 只",
                help="#063/#065 中位连板晋级跟踪",
            )
    else:
        c4.metric("昨日中位股(3~4板)表现", "—")

    if recent_phases:
        seq_str = " → ".join(
            f"`{p['date'][4:]}` {p['phase']}({p['r12']*100:.0f}%)"
            for p in recent_phases
        )
        st.caption(f"📈 **近 {len(recent_phases)} 日周期演进轨迹**（记录档位序列，供次日逐项核对）：{seq_str}")

    if laws:
        with st.expander("相关方法与观察提示"):
            st.caption("以下为作者经验对应的观察情形，不是对下一交易日的预测。")
            for law in laws:
                st.markdown(f"**{law.get('title', '')}**")
                st.write(law.get("summary", ""))


# ================= 区块 2：六大晚间战法选股池 =================

def _render_pool_table(pool: dict, pool_key: str) -> None:
    st.markdown(f"**📖 规则筛选**：{pool.get('methodology_note', '—')}")
    st.caption(f"🎯 **次日盘中观察锚点**：{pool.get('next_day_anchor', '—')}")

    # 若是二板与同身位组，先展示 #049 同身位竞价分组卡片
    pk_groups = pool.get("pk_groups") or []
    if pk_groups:
        st.markdown("##### ⚔️ 高标身位梯队与 #049 同身位分组")
        for grp in pk_groups:
            h_val = grp.get("height")
            cnt = grp.get("count", 0)
            hint = grp.get("rule_hint", "")
            names = "　｜　".join(
                f"**{s['name']}**(`{s['code']}` · {s.get('industry', '—')} · 首封 {s.get('first_time', '—')})"
                for s in (grp.get("stocks") or [])
            )
            with st.container(border=True):
                st.markdown(f"**🏆 {h_val}连板梯队（共 {cnt} 只）**：{names}")
                st.caption(hint)

    items = [it for it in (pool.get("items") or []) if not ep.is_bj_stock(it.get("code"))]
    if not items:
        st.info("当日归档中没有股票命中这组规则；数量为零也是一项实际结果。")
        return

    rows = []
    for it in items:
        code = str(it.get("code") or "")
        r: dict = {
            "代码": code,
            "名称": it.get("name") or code,
            "所属行业": it.get("industry") or "—",
        }
        if pool_key in ("pool_10am_first", "pool_2b_pk", "pool_ice_rebound"):
            r["连板数"] = int(it.get("limit_times") or 0)
            r["首封时间"] = it.get("first_time") or "—"
            r["炸板次数"] = it.get("zbc") if it.get("zbc") is not None else "—"
            r["封单(亿)"] = _fmt_num(it.get("seal_yi"), 2)
        if pool_key == "pool_ice_rebound":
            r["同板块涨停家数"] = it.get("sector_zt_count", "—")
            r["形态定位"] = it.get("pattern_type") or "—"
        if pool_key == "pool_ma6_pullback":
            r["近期最高板"] = f"{it.get('max_boards', '—')}板"
            r["前次涨停日"] = it.get("last_zt_date") or "—"
            r["今日涨跌"] = _fmt_pct(it.get("pct_chg"))
            r["MA6 / 距离"] = f"{_fmt_num(it.get('ma6'))} ({_fmt_pct(it.get('dist_ma6_pct'), 1)})"
            r["MA12 / 距离"] = f"{_fmt_num(it.get('ma12'))} ({_fmt_pct(it.get('dist_ma12_pct'), 1)})"
        if pool_key == "pool_rubbing_breakout":
            r["战法形态"] = it.get("pattern") or "—"
            r["今日涨跌"] = _fmt_pct(it.get("pct_chg"))
            r["距MA6"] = _fmt_pct(it.get("dist_ma6_pct"), 1)
            r["距MA12"] = _fmt_pct(it.get("dist_ma12_pct"), 1)
        if pool_key == "pool_trend_catchup":
            r["首封时间"] = it.get("first_time") or "—"
            r["板块趋势锚点"] = it.get("trend_anchors") or "—"

        r["最新价"] = _fmt_num(it.get("close"), 2)
        r["流通市值(亿)"] = _fmt_num(it.get("circ_mv_yi"), 1)
        r["战法标签"] = " · ".join(it.get("tags") or []) or "—"
        r["风险标签"] = " · ".join(it.get("risks") or []) if it.get("risks") else "未记录风险标签"
        r["行情链接"] = _xueqiu_link(code)
        rows.append(r)

    df_show = pd.DataFrame(rows)
    st.dataframe(
        df_show,
        hide_index=True,
        use_container_width=True,
        column_config={
            "行情链接": st.column_config.LinkColumn("雪球K线", display_text="查看K线"),
        },
    )
    journey.candidate_actions(
        df_show,
        key=f"ep_pool_{pool_key}",
        code_col="代码",
        name_col="名称",
        equities=True,
    )


def _render_six_pools(plan: dict) -> None:
    st.markdown("### 策略选股池")
    st.caption(
        "标的由收盘后的明确条件筛出，并附带 **#096 换手阈值、#176 长上影、#004 一字缺换手、#032 午后封板过程** 等风险标签。标签是待核对信息，不是已完成排雷。"
    )
    pools = plan.get("pools") or {}
    order = [
        ("pool_10am_first", "⚡ 10点前首板 (#125)"),
        ("pool_2b_pk", "🐉 换手二板与同身位 (#033/#049)"),
        ("pool_ice_rebound", "🦴 多次炸板回封 (#080/#118)"),
        ("pool_ma6_pullback", "📐 高位股回踩MA6/12 (#128)"),
        ("pool_rubbing_breakout", "🌀 长影线与突破回踩 (#169/#172)"),
        ("pool_trend_catchup", "🚀 强行业中的首板 (#077/#103)"),
    ]
    labels = [f"{lbl} [{pools.get(k, {}).get('count', 0)}]" for k, lbl in order]
    tabs = st.tabs(labels)
    for tab, (k, _) in zip(tabs, order):
        with tab:
            p = pools.get(k) or {}
            _render_pool_table(p, k)


# ================= 区块 3：次日盘中 5 大时间节点沙盘 =================

def _render_intraday_timeline(plan: dict) -> None:
    st.markdown("### 盘中观察参考")
    timeline = plan.get("intraday_timeline") or []
    if not timeline:
        st.info("暂无盘中时间轴数据。")
        return
    rows = []
    for node in timeline:
        rows.append({
            "时间窗口": node.get("time_window", "—"),
            "关键节点与法则": node.get("node_name", "—"),
            "盘中核心观察动作": node.get("core_task", "—"),
            "今日映射关注标的": node.get("watch_targets", "—"),
            "条件失效或重点核对": node.get("red_line", "—"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ================= 区块 4：VIP 专属——战法历史成绩单 & 自选股战法共振诊断 =================

def _render_vip_scorecard_and_watchlist(plan: dict) -> None:
    st.markdown("---")
    st.markdown("### 💎 VIP 专属：规则历史记录 × 个人自选股核对")
    st.caption(
        "免费层公开每日候选池；**VIP 会员解锁「不同情绪周期下的规则晋级记录」与「个人自选股 MA6/MA12、风险标签核对（含每晚微信主动推送）」**。"
    )

    sc = plan.get("pool_scorecard") or {}
    strats = sc.get("strategies") or {}
    s_pre = strats.get("pre_10am_first") or {}
    s_t2 = strats.get("turnover_2b") or {}

    # 免费可见的顶层历史记录摘要；只呈现计数与结果，不宣称规则优势。
    if sc.get("status") == "ok":
        c1, c2, c3 = st.columns(3)
        r_pre = s_pre.get("promo_rate")
        r_post = s_pre.get("benchmark_rate")
        edge_pre = s_pre.get("edge_pct")
        c1.metric(
            "#125 10:00前首板历史 1进2 晋级率",
            _fmt_pct(r_pre * 100 if r_pre is not None else None, 1, sign=False),
            delta=(f"与10点后首板相差 {edge_pre:+.1f}个百分点" if edge_pre is not None else None),
            help=f"站内近 {sc.get('trade_days', 0)} 个交易日涨停归档（晋级 {s_pre.get('promoted', 0)}/{s_pre.get('base', 0)}；10点后 {s_pre.get('benchmark_promoted', 0)}/{s_pre.get('benchmark_base', 0)}）",
        )
        r_t2 = s_t2.get("promo_rate")
        edge_t2 = s_t2.get("edge_pct")
        c2.metric(
            "#033 换手二板历史 2进3 晋级率",
            _fmt_pct(r_t2 * 100 if r_t2 is not None else None, 1, sign=False),
            delta=(f"较一字二板 {edge_t2:+.1f}个百分点" if edge_t2 is not None else None),
            help=f"样本 {s_t2.get('promoted', 0)}/{s_t2.get('base', 0)} 只",
        )
        c3.metric(
            "回验交易日跨度",
            f"{sc.get('trade_days', 0)} 天",
            delta=f"{sc.get('date_from', '—')} ~ {sc.get('date_to', '—')}",
        )

    is_vip = auth.is_vip()
    if not is_vip:
        with st.container(border=True):
            st.markdown(
                "🔒 **VIP 深度权益预览（¥25/月 · ¥60/季 · ¥200/年）**\n\n"
                "1. **四大情绪周期 × 战法晋级率对照**：并列展示「发酵期 / 分歧期 / 退潮期 / 冰点期」下 10点前首板与换手二板的归档晋级率；晋级率统计是否继续涨停，不代表实际交易胜率。\n"
                "2. **我的自选股核对**：计算全部自选股相对 `MA6 / MA12` 的百分比距离，并列出命中的 `#169 长影线`、`#096 换手阈值`、`#176 长上影`等现有标签。\n"
                "3. **每晚微信主动推送**：收盘跑批完成后，将当日规则候选与你的自选股核对结果推送到微信。"
            )
            if st.button("💎 前往会员中心开通 / 了解详情", key="ep_go_vip", type="primary"):
                st.switch_page("pages/dashboard.py")
        return

    # ---- VIP 已解锁内容 ----
    col_left, col_right = st.columns([1, 1])
    with col_left:
        st.markdown("#### 📊 四大情绪周期 × 归档晋级记录（VIP）")
        by_ph = sc.get("by_phase") or []
        if by_ph:
            ph_rows = []
            for p_row in by_ph:
                r1 = p_row.get("pre10_1to2_rate")
                r2 = p_row.get("turnover_2to3_rate")
                ph_rows.append({
                    "当日所处情绪周期": p_row.get("phase", "—"),
                    "收录交易日": p_row.get("trade_days", 0),
                    "#125 10点前首板(1进2)": f"{_fmt_pct(r1*100 if r1 is not None else None, 1, sign=False)} ({p_row.get('pre10_sample', '—')})",
                    "#033 换手二板(2进3)": f"{_fmt_pct(r2*100 if r2 is not None else None, 1, sign=False)} ({p_row.get('turnover_2to3_sample', '—')})",
                })
            st.dataframe(pd.DataFrame(ph_rows), hide_index=True, use_container_width=True)
            st.caption("注：按触发当日的情绪周期归类，统计次日实际连板晋级比例。")
        else:
            st.info("当前还没有可按情绪周期分开展示的归档记录。")

    with col_right:
        st.markdown("#### 🔍 我的自选股 × 规则与风险标签核对（VIP）")
        user = auth.get_current_user() or {}
        uid = user.get("id")
        wl = database.get_user_watchlist(uid) if uid else []
        if not wl:
            st.info("你尚未添加自选股。前往「⭐ 我的池子」添加后，此处会列出命中的候选规则与现有风险标签。")
        else:
            snap_df = _load_csv_cached(ep.SNAPSHOT_CSV)
            strong_df = _load_csv_cached(ep.STRONG_CSV)
            breakout_df = _load_csv_cached(ep.BREAKOUT_CSV)
            prices_df = _load_csv_cached(ep.PRICES_CSV)
            ladder_hist = _load_csv_cached(ep.LADDER_HIST_PATH)
            snap_map = ep._build_snapshot_lookup(snap_df)
            tr_map = ep._build_turnover_lookup(strong_df, breakout_df)
            diag = ep.diagnose_watchlist(
                watchlist=wl,
                pools=plan.get("pools") or {},
                snap_map=snap_map,
                turnover_map=tr_map,
                prices_df=prices_df,
                ladder_hist=ladder_hist,
                trade_date=str(plan.get("date") or ""),
            )
            st.caption(diag.get("summary", ""))
            d_rows = []
            for it in diag.get("items") or []:
                d_rows.append({
                    "代码": it["code"],
                    "名称": it["name"],
                    "最新价": _fmt_num(it.get("close"), 2),
                    "MA6/MA12 均线定位 (#128)": it.get("ma_status") or "—",
                    "命中候选池": " · ".join(it.get("pool_hits") or []) or "未命中",
                    "现有风险标签": " · ".join(it.get("risks") or []) or "未记录风险标签",
                })
            st.dataframe(pd.DataFrame(d_rows), hide_index=True, use_container_width=True)

    with st.expander("📲 微信推送绑定状态（每晚接收候选池与自选股核对）", expanded=False):
        user = auth.get_current_user() or {}
        if user.get("id") and pb.ensure_app_token():
            pb.render(user["id"], key_prefix="ep_vip")
        else:
            st.info("请先登录账号以管理微信推送绑定。")


# ================= 区块 5：次日观察计划制定器（#102 四分法 & #165 两个数据） =================

def _render_plan_builder(plan: dict) -> None:
    st.markdown("### 观察备忘录")
    st.caption("从各池选择要继续跟踪的股票；入选只表示命中归档观察条件，下一步仍需逐项核对。")
    pools = plan.get("pools") or {}
    all_opts = []
    opt_meta = {}
    for p_key, p_obj in pools.items():
        p_short = str(p_obj.get("title") or p_key).split("（")[0]
        for it in (p_obj.get("items") or []):
            label = f"{it['name']} ({it['code']}) — {p_short}"
            if label not in opt_meta:
                all_opts.append(label)
                opt_meta[label] = (it, p_obj.get("next_day_anchor", ""), p_short, p_key)

    from stock_search import stock_label
    selected = st.multiselect(
        "选择观察标的",
        options=all_opts,
        format_func=lambda label: stock_label(opt_meta[label][0]['name'], opt_meta[label][0]['code'], opt_meta[label][2]),
        default=[],
        key="ep_plan_picks",
        placeholder="输入名称、代码或拼音首字母",
    )

    if selected:
        lines = [f"【{plan.get('date_display', '')} 晚间复盘 · 次日观察计划】"]
        for idx, lbl in enumerate(selected, 1):
            it, anchor, _, _ = opt_meta[lbl]
            tags_str = " / ".join(it.get("tags") or [])
            risk_str = f" ｜ 注意：{' '.join(it['risks'])}" if it.get("risks") else ""
            lines.append(
                f"{idx}. {it['name']}({it['code']} · {it.get('industry', '—')})：{tags_str}{risk_str}\n"
                f"   · 下一交易日核对：{anchor}"
            )
        generated_text = "\n".join(lines)
        st.markdown("#### 冻结当前依据并继续跟踪")
        st.code(generated_text, language="markdown")

        action_options = {}
        for label in selected:
            item, _, _, _ = opt_meta[label]
            code = journey.equity_symbol(item.get("code"))
            if code:
                action_options[code] = stock_label(item['name'], code)
        if not action_options:
            st.warning("所选记录缺少可识别的证券代码，无法带入其他页面；仍可复制或下载本段记录。")
            action_code = ""
        elif len(action_options) == 1:
            action_code = next(iter(action_options))
        else:
            action_code = st.selectbox(
                "选择后续要操作的标的",
                list(action_options),
                format_func=action_options.get,
                key="ep_plan_action_symbol",
            )

        c1, c2, c3 = st.columns(3)
        if c1.button(
            "加入观察记录并打开 →",
            key="ep_plan_add_tracking",
            type="primary",
            disabled=not action_code,
        ):
            item, anchor, pool_name, pool_key = opt_meta[selected[0]]
            if action_code != journey.equity_symbol(item.get("code")):
                item, anchor, pool_name, pool_key = next(
                    meta
                    for label, meta in opt_meta.items()
                    if journey.equity_symbol(meta[0].get("code")) == action_code
                )
            tags = [str(tag) for tag in (item.get("tags") or []) if str(tag).strip()]
            reasons = [f"{pool_name}：{' / '.join(tags)}"] if tags else [pool_name]
            reasons.append(f"下一交易日核对：{anchor or '待补充'}")
            context = {
                "symbol": action_code,
                "name": item.get("name") or "",
                "industry": item.get("industry") or "",
                "reasons": reasons,
                "risks": list(item.get("risks") or []),
                "sources": [
                    f"晚间复盘 {plan.get('date') or plan.get('date_display') or ''} · {pool_name}"
                ],
            }
            try:
                import research_store
                pool_style = {
                    "pool_10am_first": "relay",
                    "pool_2b_pk": "relay",
                    "pool_ice_rebound": "reversal",
                    "pool_ma6_pullback": "reversal",
                    "pool_rubbing_breakout": "trend",
                    "pool_trend_catchup": "trend",
                }.get(pool_key, "trend")
                saved, added, save_error = research_store.follow(
                    {
                        "symbol": action_code,
                        "name": item.get("name") or "",
                        "industry": item.get("industry") or "",
                        "date": plan.get("date") or "",
                        "reasons": reasons,
                        "risks": list(item.get("risks") or []),
                        "sources": [f"data/evening_plan/latest.json:{pool_key}"],
                    },
                    pool_style,
                    {},
                    False,
                )
                st.session_state[research_store.key()+"_selected_record"] = saved["id"]
                st.session_state[research_store.key()+"_flash"] = (bool(save_error), research_store.save_message(saved, save_error))
            except ValueError as exc:
                st.error(f"暂时无法加入观察记录：{exc}")
                return
            journey.go(journey.WORKBENCH_PAGE, action_code, context=context)
        if c2.button(
            "带入复盘答卷 →",
            key="ep_plan_send_review",
            disabled=not action_code,
        ):
            st.session_state["observation_review_text"] = {
                "owner": journey._plans_key(),
                "text": generated_text,
            }
            journey.go(journey.REVIEW_PAGE)
        c3.download_button(
            "下载 Markdown",
            generated_text,
            file_name=f"evening-observation-{plan.get('date', 'latest')}.md",
            mime="text/markdown",
            key="ep_plan_download",
        )


# ================= 区块 6：181 篇方法论结构化检索库 =================

def _render_methodology_library() -> None:
    kb = _load_kb()
    articles = kb.get("articles") or []
    if not articles:
        st.warning("方法论知识库文件（data/methodology_181.json）未找到。")
        return

    st.markdown("### 📚 181 篇交易方法资料库")
    st.caption(
        f"共收录 **{len(articles)}** 篇作者原文与整理摘要，按「晚间复盘五步法」和 13 个专题归档。"
        "「已接入候选或风险标签流程」只表示相关字段进入了站内筛选、观察或风险标记流程，不表示规则有效，也不代表"
        "作者判断或站内生成摘要已经过本站回测验证。"
        f"　｜　_{kb.get('disclaimer', ep.DISCLAIMER)}_"
    )
    st.info(
        "资料库用于追溯方法出处和检索观察条件。作者原文中的因果判断、胜率、盈亏比与操作措辞"
        "仍保持原貌；站内不会把生成式摘要改写为已证实结论。"
    )

    c1, c2, c3, c4 = st.columns([1.4, 1.4, 1.2, 1.5])
    steps = ["全部步骤"] + (kb.get("evening_steps") or [])
    cats = ["全部专题"] + (kb.get("categories") or [])
    sel_step = c1.selectbox("按资料主题筛选", steps, key="kb_step")
    sel_cat = c2.selectbox("按 13 个资料专题筛选", cats, key="kb_cat")
    only_quant = c3.checkbox("仅看已进入候选或风险标签流程的条目", value=False, key="kb_quant")
    kw = c4.text_input("🔍 关键词搜索（如：十点前 / 长影线 / 回封 / MA6 / 同身位）", value="", key="kb_kw").strip()

    filtered = []
    for a in articles:
        if sel_step != "全部步骤" and a.get("evening_step") != sel_step:
            continue
        if sel_cat != "全部专题" and a.get("category_name") != sel_cat:
            continue
        if only_quant and not a.get("quantifiable"):
            continue
        if kw:
            hay = f"{a.get('id')} {a.get('title')} {a.get('core_rule')} {a.get('evening_application')} {a.get('content')}".lower()
            if kw.lower() not in hay:
                continue
        filtered.append(a)

    st.caption(f"当前筛选命中 **{len(filtered)}** 条资料（展示前 40 条，可通过上方搜索框精准定位）：")

    for a in filtered[:40]:
        badge = "🧩 已接入候选或风险标签流程" if a.get("quantifiable") else "📚 资料归档，未接入候选流程"
        header = f"#{a['id']} 《{a['title']}》 ｜ {a.get('evening_step', '')} · {a.get('category_name', '')} [{badge}]"
        with st.expander(header, expanded=(len(filtered) <= 5)):
            st.markdown(
                f"**📝 站内整理摘要（作者立场，未经本站验证）**：{a.get('core_rule', '—')}"
            )
            pool_key = str(a.get("pool_key") or "")
            if pool_key:
                pool_names = {
                    "pool_10am_first": "10:00 前换手首板池",
                    "pool_2b_pk": "换手二板与同身位组",
                    "pool_ice_rebound": "多次炸板回封与触板回落池",
                    "pool_ma6_pullback": "高位股回踩 MA6 / MA12 池",
                    "pool_rubbing_breakout": "长影线组合与突破回踩池",
                    "pool_trend_catchup": "强行业中的低位首板池",
                    "cycle_law": "情绪与日历观察记录",
                    "risk_gate": "风险标记规则",
                }
                st.markdown(f"**🧩 站内接入位置**：{pool_names.get(pool_key, pool_key)}")
                st.caption(
                    "接入位置只说明该条目关联了结构化字段。实际筛选阈值与当日命中记录见对应候选池；"
                    "规则是否具有预测力仍需由持续归档结果核对。"
                )
            else:
                st.caption("本条未接入候选池或风险标签；下方原文只用于追溯作者立场与方法出处。")
            if a.get("content"):
                st.markdown("---")
                st.markdown("**📚 作者原文归档（保留原意，不作为站内结论）**")
                st.markdown(a["content"])


# ================= 页面主入口 =================

def render_evening_plan_page() -> None:
    st.title("晚间复盘与计划")
    plan = _load_plan()
    if plan and plan.get("status") != "failed":
        _render_freshness_and_disclaimer(plan)
    sections = ["选股池", "情绪记录", "盘中参考", "观察备忘录", "历史对照", "方法资料"]
    descriptions = {
        "选股池": "收盘规则筛出的候选与风险标签",
        "情绪记录": "晋级、亏钱效应与周期档位",
        "盘中参考": "时间窗口与需要核对的盘面变化",
        "观察备忘录": "从候选池生成自己的关注清单",
        "历史对照": "归档结果、自选股核对与规则表现",
        "方法资料": "作者原文与已结构化规则出处",
    }
    if plan and plan.get("status") != "failed":
        pool_total = sum(int((p or {}).get("count") or 0) for p in (plan.get("pools") or {}).values())
        phase = (plan.get("cycle_and_laws") or {}).get("current_phase") or "—"
        st.caption(
            f"数据 {plan.get('date_display') or plan.get('date') or '—'} · "
            f"统计分组 {phase} · 各池候选合计 {pool_total} 条（跨池重复计）"
        )
    raw_requested = str(st.query_params.get("section", "选股池"))
    requested = raw_requested if raw_requested in sections else "选股池"
    if raw_requested != requested:
        st.query_params["section"] = requested
    if st.session_state.get("_evening_section_url") != requested:
        st.session_state["evening_section"] = requested
        st.session_state["_evening_section_url"] = requested
    section = st.segmented_control(
        "内容",
        sections,
        selection_mode="single",
        label_visibility="collapsed",
        key="evening_section",
    ) or requested
    if section != requested:
        st.query_params["section"] = section
    st.caption(descriptions[section])
    if section == "方法资料":
        _render_methodology_library()
    elif not plan or plan.get("status") == "failed":
        st.info("该日期尚无晚间归档。")
    else:
        {"选股池": _render_six_pools, "情绪记录": _render_cycle_and_laws,
         "盘中参考": _render_intraday_timeline, "观察备忘录": _render_plan_builder,
         "历史对照": _render_vip_scorecard_and_watchlist}[section](plan)

# -*- coding: utf-8 -*-
"""
「晚间复盘与次日交易计划」展示页（page_evening_plan.py）

架构定位：
  - 纯展示层：只读取跑批产物 data/evening_plan/latest.json 与 data/methodology_181.json，
    以及调用计算层 evening_plan.py 做纯内存结构映射；绝不直连外部行情 API（无 tushare/akshare/requests/urllib）。
  - 免费层 vs VIP 付费层（遵循全站「锁送达、锁个人视角、锁历史验证，不锁公开客观池」原则）：
      · 免费公开：每日 6 大客观战法选股池、情绪周期与日历特殊法则雷达、次日盘中 5 大时间节点沙盘、181 篇方法论检索库。
      · VIP 专属：
          1) 四大情绪周期 × 战法历史晋级率矩阵（告诉你「当前周期哪种战法胜率最高、哪种必须停手」）
          2) 个人自选股 × 六大战法共振与四道防雷质检诊断（并随每晚微信摘要主动推送到手机）
          3) 个人复盘历史对比与 #165「两个数据（胜率与盈亏比）」回验账本
"""
from __future__ import annotations

import json
import os
import re

import pandas as pd
import streamlit as st

import auth
import data_freshness as freshness
import database
import evening_plan as ep
import push_binding as pb


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
def _load_plan() -> dict | None:
    return ep.load_evening_plan()


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
        st.error(
            f"⚠️ **晚间战法池数据可能未更新：{v['reason']}。** "
            f"请按 {shown} 这个交易日理解页内所有标的——它不是最新交易日的结果。",
            icon="🚨",
        )
    elif v["status"] == "unknown":
        st.warning("⚠️ 晚间战法池缺少有效日期戳，请谨慎参考。")
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

    st.markdown("### 🧭 Step 1 & 2：情绪周期定位与特殊日历法则")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("当前情绪周期", f"{cur_phase}", help="与全市场看板自适应分位阈值同源（#063/#081）")
    c2.metric("日历节点", f"{weekday}", help="用于判定 #121 周五高标效应、#145 周五二板空间龙与 #027 节后定律")

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
            help="#125 十点前涨停定律：对比昨日 10:00 前封板与 10:00 后封板在今日的实际 1进2 晋级率",
        )
    else:
        c3.metric("昨日 10:00前首板晋级率", "—", help=str(split_125.get("reason") or "暂无对比样本"))

    if mid_065.get("status") in ("ok", "partial"):
        loss_r = mid_065.get("big_loss_rate")
        mid_n = mid_065.get("mid_n", 0)
        if loss_r is not None:
            c4.metric(
                "昨日中位股(3~4板)大面率",
                _fmt_pct(loss_r * 100, 0, sign=False),
                delta=("⚠️ 亏钱扩散" if mid_065.get("warning") else "承接温和"),
                delta_color="inverse" if mid_065.get("warning") else "normal",
                help="#065 亏钱效应的扩散：中位股（3~4板）是退潮杀跌的第一传导区",
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
        st.caption(f"📈 **近 {len(recent_phases)} 日周期演进轨迹**（用于识别 #097 双冰强反 与 #081 退潮 D3 陷阱）：{seq_str}")

    if laws:
        for lw in laws:
            lvl = lw.get("level", "info")
            title = lw.get("title", "")
            summary = lw.get("summary", "")
            if lvl == "opportunity":
                st.success(f"🟢 **{title}**：{summary}")
            elif lvl == "warning":
                st.warning(f"⚠️ **{title}**：{summary}")
            else:
                st.info(f"📌 **{title}**：{summary}")
    else:
        st.info("📌 今日未触发极端冰点/高潮或特殊日历预警，按常规周期结构观察。")


# ================= 区块 2：六大晚间战法选股池 =================

def _render_pool_table(pool: dict, pool_key: str) -> None:
    st.markdown(f"**📖 战法逻辑**：{pool.get('methodology_note', '—')}")
    st.caption(f"🎯 **次日盘中观察锚点**：{pool.get('next_day_anchor', '—')}")

    # 若是二板与多龙同台池，先展示 #049 多龙同台竞价 PK 卡片
    pk_groups = pool.get("pk_groups") or []
    if pk_groups:
        st.markdown("##### ⚔️ 高标身位梯队 & #049 多龙同台《中庸》观察组")
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
        st.info("今日盘面无满足该战法客观条件的标的（宁缺毋滥，不强行凑数）。")
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
        r["防雷质检"] = " · ".join(it.get("risks") or []) if it.get("risks") else "✅ 正常"
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


def _render_six_pools(plan: dict) -> None:
    st.markdown("### 🎯 Step 3 & 4：六大晚间客观战法选股池")
    st.caption(
        "所有标的均由收盘后客观规则自动筛选，并经过 **#096 次新死亡换手率、#176 高位双针顶、#004 一字筹码断层、#032 午后烂板** 四道防雷质检门。"
    )
    pools = plan.get("pools") or {}
    order = [
        ("pool_10am_first", "⚡ 10点前首板 (#125)"),
        ("pool_2b_pk", "🐉 换手二板&多龙PK (#033/#049)"),
        ("pool_ice_rebound", "🦴 狗啃板&反包 (#080/#118)"),
        ("pool_ma6_pullback", "📐 龙头首阴MA6/12 (#128)"),
        ("pool_rubbing_breakout", "🌀 揉搓线&突破回踩 (#169/#172)"),
        ("pool_trend_catchup", "🚀 强趋势补涨首板 (#077/#103)"),
    ]
    labels = [f"{lbl} [{pools.get(k, {}).get('count', 0)}]" for k, lbl in order]
    tabs = st.tabs(labels)
    for tab, (k, _) in zip(tabs, order):
        with tab:
            p = pools.get(k) or {}
            _render_pool_table(p, k)


# ================= 区块 3：次日盘中 5 大时间节点沙盘 =================

def _render_intraday_timeline(plan: dict) -> None:
    st.markdown("### ⏱️ Step 5：次日盘中 5 大时间锚点观察沙盘")
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
            "风控纪律红线": node.get("red_line", "—"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ================= 区块 4：VIP 专属——战法历史成绩单 & 自选股战法共振诊断 =================

def _render_vip_scorecard_and_watchlist(plan: dict) -> None:
    st.markdown("---")
    st.markdown("### 💎 VIP 专属：战法历史验证成绩单 × 个人自选股战法共振诊断")
    st.caption(
        "免费层公开每日客观选股池；**VIP 会员解锁「不同情绪周期下的战法真实胜率矩阵」与「个人自选股 MA6/MA12 + 四道防雷自动诊断（含每晚微信主动推送）」**。"
    )

    sc = plan.get("pool_scorecard") or {}
    strats = sc.get("strategies") or {}
    s_pre = strats.get("pre_10am_first") or {}
    s_t2 = strats.get("turnover_2b") or {}

    # 免费可见的顶层实证摘要（证明方法论在真实数据上的有效性，吸引转化）
    if sc.get("status") == "ok":
        c1, c2, c3 = st.columns(3)
        r_pre = s_pre.get("promo_rate")
        r_post = s_pre.get("benchmark_rate")
        edge_pre = s_pre.get("edge_pct")
        c1.metric(
            "#125 10:00前首板历史 1进2 晋级率",
            _fmt_pct(r_pre * 100 if r_pre is not None else None, 1, sign=False),
            delta=(f"领先10点后首板 {edge_pre:+.1f}个百分点" if edge_pre is not None else None),
            help=f"基于站内近 {sc.get('trade_days', 0)} 个交易日涨停归档实测（样本 {s_pre.get('promoted', 0)}/{s_pre.get('base', 0)} vs 10点后 {s_pre.get('benchmark_promoted', 0)}/{s_pre.get('benchmark_base', 0)}）",
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
                "1. **四大情绪周期 × 战法胜率矩阵**：直观对比「发酵期 / 分歧期 / 退潮期 / 冰点期」下 10点前首板与换手二板的真实晋级率，看清何时重拳出击、何时严禁追高。\n"
                "2. **我的自选股 × 六大战法共振与四道防雷诊断**：自动计算你的全部自选股距离 `MA6 / MA12` 支撑位百分比、是否走出 `#169 揉搓线`，以及是否触碰 `#096 次新死亡换手率`、`#176 高位双针顶`。\n"
                "3. **每晚微信主动推送**：收盘跑批完成后，将今日晚间战法池精选 + 你的自选股专属诊断直接推送到微信，无需每晚手动刷网页。"
            )
            if st.button("💎 前往会员中心开通 / 了解详情", key="ep_go_vip", type="primary"):
                st.switch_page("pages/dashboard.py")
        return

    # ---- VIP 已解锁内容 ----
    col_left, col_right = st.columns([1, 1])
    with col_left:
        st.markdown("#### 📊 四大情绪周期 × 战法真实晋级率矩阵（VIP）")
        by_ph = sc.get("by_phase") or []
        if by_ph:
            ph_rows = []
            for p_row in by_ph:
                r1 = p_row.get("pre10_1to2_rate")
                r2 = p_row.get("turnover_2to3_rate")
                ph_rows.append({
                    "当日所处情绪周期": p_row.get("phase", "—"),
                    "样本天数": p_row.get("trade_days", 0),
                    "#125 10点前首板(1进2)": f"{_fmt_pct(r1*100 if r1 is not None else None, 1, sign=False)} ({p_row.get('pre10_sample', '—')})",
                    "#033 换手二板(2进3)": f"{_fmt_pct(r2*100 if r2 is not None else None, 1, sign=False)} ({p_row.get('turnover_2to3_sample', '—')})",
                })
            st.dataframe(pd.DataFrame(ph_rows), hide_index=True, use_container_width=True)
            st.caption("注：按触发当日的情绪周期归类，统计次日实际连板晋级比例。")
        else:
            st.info("战法分周期样本正在积累中。")

    with col_right:
        st.markdown("#### 🔍 我的自选股 × 晚间战法共振与防雷诊断（VIP）")
        user = auth.get_current_user() or {}
        uid = user.get("id")
        wl = database.get_user_watchlist(uid) if uid else []
        if not wl:
            st.info("你尚未添加自选股。前往「⭐ 我的池子」添加自选股后，此处与每晚微信推送将自动生成专属战法与排雷诊断。")
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
                    "命中晚间战法池": " · ".join(it.get("pool_hits") or []) or "未命中",
                    "防雷质检门": " · ".join(it.get("risks") or []) or "✅ 正常",
                })
            st.dataframe(pd.DataFrame(d_rows), hide_index=True, use_container_width=True)

    with st.expander("📲 微信推送绑定状态（每晚自动接收晚间战法精选 + 自选股共振诊断）", expanded=False):
        user = auth.get_current_user() or {}
        if user.get("id") and pb.ensure_app_token():
            pb.render(user["id"], key_prefix="ep_vip")
        else:
            st.info("请先登录账号以管理微信推送绑定。")


# ================= 区块 5：次日观察计划制定器（#102 四分法 & #165 两个数据） =================

def _render_plan_builder(plan: dict) -> None:
    st.markdown("### 📝 制定我的次日观察计划（#102 四分法分批 × #165 胜率与盈亏比）")
    st.caption(
        "依据 **#102 四分法分批管理** 与 **#165 两个数据（胜率与盈亏比）**：每晚从六大战法池中精选 **不超过 4 只**（2只主看 + 2只备选）标的制定次日触发条件，杜绝盘中临时起意追高。"
    )
    pools = plan.get("pools") or {}
    all_opts = []
    opt_meta = {}
    for p_key, p_obj in pools.items():
        p_short = str(p_obj.get("title") or p_key).split("（")[0]
        for it in (p_obj.get("items") or [])[:10]:
            label = f"{it['name']} ({it['code']}) — {p_short}"
            if label not in opt_meta:
                all_opts.append(label)
                opt_meta[label] = (it, p_obj.get("next_day_anchor", ""))

    selected = st.multiselect(
        "从今晚六大战法池中勾选明日重点观察标的（建议精选 2~4 只）：",
        options=all_opts,
        default=all_opts[:2] if len(all_opts) >= 2 else all_opts,
        max_selections=6,
        key="ep_plan_picks",
    )

    if selected:
        lines = [f"【{plan.get('date_display', '')} 晚间复盘 · 次日观察计划】"]
        for idx, lbl in enumerate(selected, 1):
            it, anchor = opt_meta[lbl]
            tags_str = " / ".join(it.get("tags") or [])
            risk_str = f" ｜ 注意：{' '.join(it['risks'])}" if it.get("risks") else ""
            lines.append(
                f"{idx}. {it['name']}({it['code']} · {it.get('industry', '—')})：[{tags_str}]{risk_str}\n"
                f"   · 次日盘中观察条件：{anchor}"
            )
        generated_text = "\n".join(lines)
        st.text_area("已生成的结构化次日观察备忘录（可一键复制或同步至复盘答卷）：", value=generated_text, height=160)


# ================= 区块 6：181 篇方法论结构化检索库 =================

def _render_methodology_library() -> None:
    kb = _load_kb()
    articles = kb.get("articles") or []
    if not articles:
        st.warning("方法论知识库文件（data/methodology_181.json）未找到。")
        return

    st.markdown("### 📚 181 篇短线投机法则实战库（按晚间复盘五步 SOP 归档）")
    st.caption(
        f"共收录 **{len(articles)}** 篇实战法则，已按「晚间复盘五步法」与「13 大战法专题」完成结构化归并。"
        f"　｜　_{kb.get('disclaimer', ep.DISCLAIMER)}_"
    )

    c1, c2, c3, c4 = st.columns([1.4, 1.4, 1.2, 1.5])
    steps = ["全部步骤"] + (kb.get("evening_steps") or [])
    cats = ["全部专题"] + (kb.get("categories") or [])
    sel_step = c1.selectbox("按晚间复盘 SOP 步骤筛选", steps, key="kb_step")
    sel_cat = c2.selectbox("按 13 大战法分类筛选", cats, key="kb_cat")
    only_quant = c3.checkbox("仅看已接入自动选股池的核心法则", value=False, key="kb_quant")
    kw = c4.text_input("🔍 关键词搜索（如：十点前 / 揉搓线 / 狗啃板 / MA6 / 中庸）", value="", key="kb_kw").strip()

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

    st.caption(f"当前筛选命中 **{len(filtered)}** 篇法则（展示前 40 篇，可通过上方搜索框精准定位）：")

    for a in filtered[:40]:
        badge = "⚡ 已量化入池" if a.get("quantifiable") else "🧠 复盘心法"
        header = f"#{a['id']} 《{a['title']}》 ｜ {a.get('evening_step', '')} · {a.get('category_name', '')} [{badge}]"
        with st.expander(header, expanded=(len(filtered) <= 5)):
            st.markdown(f"**🎯 核心铁律**：{a.get('core_rule', '—')}")
            st.markdown(f"**🌙 晚间复盘应用**：{a.get('evening_application', '—')}")
            if a.get("content"):
                st.markdown("---")
                st.markdown(a["content"])


# ================= 页面主入口 =================

def render_evening_plan_page() -> None:
    st.title("📋 晚间复盘与次日交易计划")
    st.caption("将 181 篇短线投机方法论转化为每晚 20:00 后可直接对账的「周期定位 + 六大客观战法池 + 四道防雷质检 + 次日盘中时间轴」")

    plan = _load_plan()
    if plan and plan.get("status") != "failed":
        _render_freshness_and_disclaimer(plan)
    else:
        st.caption(f"ℹ️ {ep.DISCLAIMER}")

    main_tabs = st.tabs([
        "🎯 晚间战法选股池 & 次日盘中沙盘",
        "✍️ 引导式复盘答卷 & AI 对账",
        "📚 181 投机法则实战库",
    ])

    with main_tabs[0]:
        if not plan or plan.get("status") == "failed":
            st.warning(
                f"⚠️ 当日晚间战法池尚未生成（{(plan or {}).get('reason', '请先运行 daily_evening_plan.py')}）。"
                "您可以先在右侧切换至「📚 181 投机法则实战库」查阅方法论。"
            )
        else:
            _render_cycle_and_laws(plan)
            st.markdown("---")
            _render_six_pools(plan)
            st.markdown("---")
            _render_intraday_timeline(plan)
            st.markdown("---")
            _render_plan_builder(plan)
            _render_vip_scorecard_and_watchlist(plan)

    with main_tabs[1]:
        from page_review import render_review_page
        render_review_page()

    with main_tabs[2]:
        _render_methodology_library()

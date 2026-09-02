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

第 4 条与第 5 条是 2026-09-01 追加的，起因是有人指出「RPS 不该产生稳定反向超额」，
排查后确认计算无误，问题出在解读口径上：
4. 必须把 **beta 敞口** 从超额收益里剥出来。动量榜天然是高 beta 组合，
   `ret - 1.0 × bench` 会把「波动放大的代价」误记成「选股能力为负」。
5. 必须报 **有效样本量**。T+N 窗口逐日滚动重叠、同日上榜标的高度相关，
   几千条记录折算后往往只剩个位数独立观测，此时任何方向结论都不成立。
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

**⚠️ 这个减法有个隐含假设，必须先说破**：`个股涨幅 − 1.0 × 基准涨幅`
等于假定组合的 beta 恰好是 1。而 RPS 榜按定义筛的就是「涨幅排在全市场前 13%」
的标的，天然扎堆在高波动的小盘成长股，实测 beta 远大于 1。
于是在大盘下跌的区间里，组合按 beta 放大出来的跌幅会被**整笔算进「超额收益」**——
那是承担了更高波动的代价，不是选股选错了。下面的「风险敞口拆解」就是把这两部分分开，
只有 beta 调整后剩下的那一截，才勉强能称为选股能力。

**T+N 的起点**：以**上榜当日收盘价**为基准点。你在当晚看到榜单，次日开盘才能买，
所以这个口径**不等于你实际能拿到的收益**——它衡量的是「榜单的排序有没有信息量」，
而不是「照着买能赚多少」。这一点必须先说清楚。

**为什么用中位数**：均值会被一两只翻倍妖股拉飞，看起来很美但复现不了。
中位数代表「从榜单里随手挑一只」的典型体验，更接近你的真实处境。

**方向正确率**：榜单是看多信号，所以只有**跑赢沪深300** 才算方向正确。
跟着大盘一起涨但没跑赢，记为错。

**区分度检验**：如果排序真有信息量，前 10 名的表现应当好于 11-30 名，
后者应当好于 31-50 名。若这个顺序不成立，说明当前样本里看不出区分能力——
这条自检比任何漂亮数字都重要，不通过我也会照实写在下面。

**样本门槛**：单组样本少于 {sc.MIN_SAMPLE} 条，一律标注为噪音。
但更要紧的是「有效样本量」：T+N 窗口逐日滚动、相邻入选日的持有期高度重叠，
同一天上榜的几十只票又同涨同跌，所以**几千条记录折算下来往往只有个位数独立观测**。
低于 {sc.MIN_INDEPENDENT} 个独立观测时，无论数字是正是负都判不出方向，
页面会直接标成「不显著」，而不是拿虚高的样本量说"稳定跑赢/跑输"。
            """
        )


def _render_beta(entry: dict) -> None:
    """风险敞口拆解：把「高 beta 在跌市放大跌幅」从「选股能力」里剥出来。"""
    b = entry.get("beta") or {}
    st.markdown("**风险敞口拆解（beta 校正）**")
    status = b.get("status")
    if not status:
        st.info("⏳ 该项由跑批生成，下一次每日跑批后会出现在这里。")
        return
    if status == "failed":
        # 归因明确的报错比「暂时无法计算」这种含混说法更有价值：
        # failed 是数据缺失（要去修数据），insufficient 是样本不够（只需等），
        # 两者的处置动作完全不同，混成一句话会让人往错的方向查。
        st.error(f"❌ 无法分离 beta：{b.get('reason', '收益数据缺失')}")
        return
    if status != "ok":
        st.info(f"⏳ {b.get('reason', '入选日不足，暂时无法分离 beta')}")
        return

    beta = float(b.get("beta", float("nan")))
    c1, c2, c3 = st.columns(3)
    c1.metric("组合隐含 beta", f"{beta:.2f}",
              help="以入选日为观测单位，用「当日上榜组收益中位数」对「同期基准收益」做回归得到的斜率")
    c2.metric("原始超额（假定 beta=1）", _pct(b.get("raw_alpha_median")),
              help="就是上方表格里的那个数，隐含 beta=1 的假设")
    c3.metric("beta 校正后超额", _pct(b.get("adj_alpha_median")),
              help="个股涨幅 − beta × 基准涨幅，剔除了「因为波动更大而多跌/多涨」的那部分")

    st.caption(
        f"回归 R² = {float(b.get('r_squared', 0)):.2f}，覆盖 {b.get('n_days', 0)} 个入选日。"
        f"beta 校正后的方向正确率 {float(b.get('adj_direction_accuracy', 0)) * 100:.1f}%；"
        f"若只取互不重叠的独立窗口（{b.get('n_independent', 0)} 个），"
        f"校正后超额中位数为 {_pct(b.get('adj_alpha_median_independent'))}、"
        f"正确率 {float(b.get('adj_direction_accuracy_independent', 0)) * 100:.1f}%。"
    )
    if beta > 1.5:
        st.warning(
            f"⚠️ 这个组合的 beta ≈ {beta:.2f}，意味着基准跌 1% 时它大致跌 {beta:.1f}%。"
            "所以上方的原始超额收益里，有相当大一块其实是**波动放大的代价**，"
            "而不是选股不准。反过来说：大盘反弹时它也会放大涨幅——"
            "这是同一枚硬币的两面，高 beta 组合本身就要求更强的择时与更小的仓位容忍度。",
            icon="⚠️",
        )


def _render_effective_sample(entry: dict) -> None:
    """有效样本量：把虚高的记录条数折算成真正独立的观测数。"""
    e = entry.get("effective_sample") or {}
    st.markdown("**有效样本量与统计显著性**")
    status = e.get("status")
    if not status:
        st.info("⏳ 该项由跑批生成，下一次每日跑批后会出现在这里。")
        return
    if status == "failed":
        st.error(f"❌ 无法折算有效样本量：{e.get('reason', '暂无可用样本')}")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("记录条数", f"{e.get('raw_n', 0)} 条", help="每只票每个入选日算一条，会大幅重复计数")
    c2.metric("折算独立观测", f"{e.get('n_independent', 0)} 个",
              help="按互不重叠的持有窗口折算，这才是统计上真正能用的样本量")
    p = e.get("p_value")
    c3.metric("双侧检验 p 值",
              "—" if p is None else f"{float(p):.2f}",
              help="检验「跑赢概率是否等于 50%」。p > 0.05 即统计上看不出方向性")

    st.caption(
        f"覆盖 {e.get('n_days', 0)} 个入选日；独立窗口中 {e.get('win_independent', 0)}"
        f"/{e.get('n_independent', 0)} 次跑赢，超额中位数 "
        f"{_pct(e.get('alpha_median_independent'))}。"
    )
    if e.get("significant"):
        st.success("✅ 该方向在统计上显著（p < 0.05），可以当作一个初步结论看待。")
    else:
        st.warning(f"⚠️ {e.get('reason', '统计上看不出方向性')}", icon="⚠️")


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

    # --- 有效样本量：必须紧跟在上面那三个数字之后 ---
    # 顺序是刻意的：先让人看到「这些数字其实只有个位数独立观测」，
    # 再往下看区分度检验，否则很容易把噪音当结论。
    st.markdown("---")
    _render_effective_sample(entry)

    # --- 风险敞口拆解 ---
    st.markdown("---")
    _render_beta(entry)

    # --- 区分度检验 ---
    st.markdown("---")
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
            st.warning(
                f"⚠️ {disc.get('verdict')}\n\n"
                "读法：这说明**在当前这段样本里**，取前 10 名和取前 50 名看不出差别，"
                "榜单更接近「一篮子强势股」而不是一个有序的优先级列表。"
                "但请对照上面的有效样本量——每档的样本同样存在窗口重叠与同日相关，"
                "所以这只是「暂未观察到区分度」，不等于「排序一定没用」。"
                "我把不好看的结果照实写在这里，而不是只挑好看的数字展示。",
                icon="⚠️",
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
            st.markdown("---")
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
            st.caption(
                "红柱=该日上榜组跑赢沪深300，绿柱=跑输。柱子越均匀分布在零轴上方，策略越稳定。"
                "**注意相邻柱子高度重叠**（T+5 口径下相邻交易日共享 4 天持有期），"
                "所以连续几根同色柱往往是同一段行情被数了多次，不是多次独立验证。"
            )


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
        f"样本门槛 {perf.get('min_sample')} 条 · 独立观测门槛 {sc.MIN_INDEPENDENT} 个。"
    )
    st.warning(
        "**风险提示与免责声明**\n\n"
        "1. 本页为**历史数据的事后统计**，不是收益承诺，也不构成投资建议、"
        "证券推荐或任何形式的买卖要约。历史表现不代表未来收益。\n"
        "2. 统计口径以**上榜当日收盘价**为起点，而榜单在收盘后才生成，"
        "因此这里的数字**不是可交易收益**，实际成交价、滑点、交易费用、"
        "涨跌停无法成交等因素均未计入。\n"
        "3. 当前归档区间很短、折算后的独立观测为个位数，"
        "任何方向性结论（无论正负）都不具备统计显著性。\n"
        "4. 榜单为程序按固定量化规则自动生成，不含人工判断，"
        "不提供目标价、买卖点与仓位建议。据此操作的风险由投资者自行承担。",
        icon="⚠️",
    )

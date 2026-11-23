"""Result-first workbench panels; public data stays local and dated."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import decision_workbench as engine
import investor_journey as journey
import market_insights as mi
from data_freshness import norm_date
from site_data import load_csv, load_json
from strategy_playbooks import PLAYBOOKS
from update_schedule import beijing_now

SOURCE_LABELS = {"data/strong_stocks.csv": "RPS强度榜", "data/breakout_stocks.csv": "阶段新高池",
                 "data/strong_etfs.csv": "ETF强度榜", "pool_rubbing_breakout": "长影线与突破回踩池",
                 "pool_trend_catchup": "强行业低位首板池", "pool_ice_rebound": "冰点期炸板回封样本池",
                 "pool_ma6_pullback": "高位股均线回踩池", "pool_10am_first": "10:00前换手首板池",
                 "pool_2b_pk": "换手二板与同身位组"}


def source_label(source):
    return SOURCE_LABELS.get(source, SOURCE_LABELS.get(source.split(":")[-1], "观察名单"))


def pct(value):
    return "—" if value is None else f"{value:+.2f}%"


def load_insights(asof):
    calendar = load_json("data/trading_calendar.json")
    days = mi.sessions(calendar, asof, 6)
    prices = load_csv("data/scorecard/prices.csv")
    plans = {d: load_json(f"data/evening_plan/history/{d}.json") for d in days}
    archives = {d: load_json(f"data/theme_history/{d}.json") for d in days[-2:]}
    # Load files with revision-aware readers first. Cache only public calculations;
    # replacing any file invalidates its content hash, even inside the TTL.
    result = _calculate(mi.RULE_VERSION, asof, calendar, prices, plans, archives,
        load_csv("data/market_sentiment.csv"), load_csv("data/funding/margin_history.csv"),
        load_csv("data/scorecard/picks.csv"), load_csv("data/sector_rotation/history.csv"),
        load_json("data/sentiment/derived.json"), load_csv("data/sentiment/breadth_history.csv"))
    result.update(ladder=load_json("data/limit_ladder.json"), attribution=load_json("data/limit_themes.json"))
    return result


@st.cache_data(ttl=600, max_entries=8, show_spinner=False)
def _calculate(rule_version, asof, calendar, prices, plans, archives, market, margin, picks, sector, sentiment, breadth):
    funding = mi.funding_readout(market, margin, calendar, asof)
    outcomes = mi.style_outcomes(picks, plans, prices, calendar, asof)
    themes = mi.theme_continuity(sector, calendar, asof)
    assessments = {style: mi.style_assessment(style, sentiment, breadth, outcomes, funding, calendar, asof) for style in PLAYBOOKS}
    return dict(calendar=calendar, prices=prices, plans=plans, archives=archives, funding=funding,
                outcomes=outcomes, themes=themes, assessments=assessments)


def render_assessment(style, analysis, *, compact=False):
    value = analysis["assessments"][style]
    render = st.warning if value["tone"] == "warning" else st.info
    render(f"**{PLAYBOOKS[style]['name']} · {value['label']}**\n\n{value['conclusion']}")
    if not compact:
        for fact in value["facts"]:
            st.write("• " + fact)
        for missing in value["missing"]:
            st.caption(missing)
    st.markdown("**接下来检查：** " + value["next_check"])


def render_outcomes(analysis):
    st.caption("核对上一交易日已归档名单的后续表现，所有类型并列展示。")
    summary = []
    for key in ("trend", "reversal", "relay", "etf"):
        series = analysis["outcomes"].get(key) or []
        row = series[-1] if series else None
        summary.append({"名单": PLAYBOOKS[key]["name"], "名单日期": row["selected_on"] if row else "—",
            "核对日期": row["date"] if row else "—", "中位涨跌幅": pct(row["median"]) if row else "—",
            "上涨占比": f"{row['up_ratio']:.1%}" if row and row["up_ratio"] is not None else "—",
            "已核对/归档": f"{row['n']}/{row['base']}" if row else "—",
            "价格缺失": row["missing"] if row else None,
            "来源缺失": len(row["missing_sources"]) if row else None})
    st.dataframe(pd.DataFrame(summary), hide_index=True, width="stretch")
    st.caption("从名单日收盘到核对日收盘的复权价格变化，未模拟成交。RPS/新高/ETF历史榜各最多归档前60只；名单可能重叠。")
    style = st.selectbox("展开名单", list(analysis["outcomes"]), format_func=lambda k: PLAYBOOKS[k]["name"], key="outcome_style")
    series = analysis["outcomes"].get(style) or []
    if not series:
        return
    st.dataframe(pd.DataFrame([{"名单日期": r["selected_on"], "核对日期": r["date"],
        "中位涨跌幅(%)": r["median"], "已核对": r["n"], "价格缺失": r["missing"],
        "沪深300同期(%)": r["benchmark"], "相差(百分点)": r["excess"]} for r in series]), hide_index=True, width="stretch")
    chosen = st.selectbox("逐股核对日期", list(range(len(series))), index=len(series)-1,
                          format_func=lambda i: series[i]["date"], key="outcome_date")
    row = series[chosen]
    if row["missing_sources"]:
        st.caption("未归档来源：" + "、".join(source_label(x) for x in row["missing_sources"]))
    st.dataframe(pd.DataFrame([{"代码": code, "涨跌幅(%)": value} for code, value in row["returns"].items()]), hide_index=True, width="stretch")


def render_today(style, analysis, evidence):
    render_outcomes(analysis)
    render_funding(analysis)


def render_funding(analysis):
    st.subheader("成交与融资")
    funding = analysis["funding"]
    c1, c2, c3 = st.columns(3)
    c1.metric("市场成交额", f"{funding['amount']:,.0f} 亿元" if funding["amount"] is not None else "缺数据")
    c2.metric("相对前20日成交均额", f"{funding['ratio']:.2f} 倍" if funding["ratio"] is not None else "缺数据")
    c3.metric("20日均额较5日前变化", pct(funding["center_change"]))
    for line in funding["implications"]:
        st.write(line)
    margin = funding["margin"]
    if margin:
        st.write(f"**沪深融资余额：{margin['balance']:,.0f} 亿元** · 数据日期 {margin['date']} · 近5交易日 {pct(margin['change5'])}")
    for missing in funding["missing"]:
        st.caption(missing)
    with st.expander("资金读数的范围与披露时间"):
        st.write("成交额衡量交易活跃度，不是净资金流入。融资余额仅覆盖沪深融资交易，"
                 "披露通常滞后一交易日；缺少任一交易所不汇总。未接入信用利差、融资续接与持仓杠杆，不能据此宣布整个负债环境恢复。")
        st.markdown("融资口径：[Tushare 融资融券交易汇总](https://tushare.pro/document/2?doc_id=58)")


def render_themes(analysis, asof):
    result = analysis["themes"]
    st.subheader("哪些方向在持续，哪些只是今天上涨？")
    follow = result.get("followthrough")
    if follow and follow["median"] is not None:
        st.info(f"上一交易日（{follow['selected_on']}）近10日强度前列的 {follow['base']} 个方向，"
                f"本日可核对 {follow['n']} 个，中位涨跌幅 **{pct(follow['median'])}**；"
                f"仍在前列 **{follow['retained']} 个**。同日题材中位涨跌幅 {pct(follow['market_median'])}。")
    rows = result["rows"]
    if not rows:
        st.info("板块历史缺失或本日快照不完整，暂时无法判断持续性。")
        return
    st.caption(f"数据日期 {asof} · 近5个交易日可用板块快照 {result['checked']}/5。按近10日涨幅前10名统计入榜次数。")
    sort_options = {"近10日涨幅": "pct10", "今日涨幅": "pct", "近5日入榜次数": "appearances", "内部上涨占比": "breadth"}
    order = st.selectbox("排序依据", list(sort_options), key="theme_sort")
    field = sort_options[order]
    rows = sorted(rows, key=lambda r: r[field] if r[field] is not None else float("-inf"), reverse=True)
    st.dataframe(pd.DataFrame([{"方向": r["name"], "今日": pct(r["pct"]),
        "近5日": pct(r["pct5"]), "近10日": pct(r["pct10"]), "近20日": pct(r["pct20"]),
        "近5日进入前10": f"{r['appearances']}/{r['observed']}次", "近5日连续在前10": f"{r['streak']}日",
        "内部上涨占比": f"{r['breadth']:.0%}" if r["breadth"] is not None else "—",
        "上涨占比较前日(百分点)": (f"{r['breadth_delta']:+.1f}"
                                          if r["breadth_delta"] is not None else "—")}
        for r in rows]), hide_index=True, width="stretch")
    options = {r["code"]: r for r in rows}
    chosen = st.selectbox("查看方向内的涨停股票", list(options),
                          format_func=lambda code: options[code]["name"], key="insight_theme")
    row = options[chosen]
    st.write(f"**{row['name']}**")
    detail = mi.theme_leader_followers(row["name"], analysis["archives"], analysis["ladder"],
        analysis["attribution"], analysis["prices"], analysis["calendar"], asof)
    if detail["leaders"]:
        groups = []
        for label, group in (("昨日最高连板样本", detail["leaders"]), ("昨日其余涨停样本", detail["followers"])):
            groups.append({"昨日身份": label, "今日中位涨跌幅": pct(group["median"]),
                           "有效价格/样本": f"{group['n']}/{group['base']}"})
        st.dataframe(pd.DataFrame(groups), hide_index=True, width="stretch")
    st.caption(detail["note"])
    members = detail["current"]
    if members:
        st.markdown("**本日明确归因到该方向的涨停股票**")
        st.dataframe(pd.DataFrame([{"代码": r["symbol"], "名称": r["name"], "连板数": r["boards"],
                                  "归因": r["theme"], "来源": r["source"]} for r in members]), hide_index=True, width="stretch")
        journey.candidate_actions(pd.DataFrame(members), code_col="symbol", name_col="name", key="theme_insight")
    else:
        st.caption("本日涨停归因中没有同名标签；未用行业名称或关键词模糊匹配补齐股票。")
    with st.expander("统计范围"):
        st.write("展示当日、前一交易日近10日涨幅前10名，以及当日涨幅前5名的并集。内部上涨占比=上涨家数/(上涨+下跌家数)，不含平盘。板块可能交叉重叠。")
        if result["missing"]:
            st.write("缺少完整快照的日期：" + "、".join(result["missing"]))


def candidate_detail(candidate, style, analysis, asof):
    symbol = candidate["symbol"]
    plans = analysis["plans"].get(asof) or {}
    points = []
    for key in PLAYBOOKS[style]["pools"]:
        for row in (plans.get("pools", {}).get(key) or {}).get("items") or []:
            if symbol_id := engine.symbol_id(row.get("code"), equity=True):
                if symbol_id != symbol:
                    continue
                for label, field in (("MA6", "ma6"), ("MA12", "ma12")):
                    value = engine.number(row.get(field))
                    if value is not None and value > 0:
                        points.append(f"{asof} 的{label}为 {value:.2f}；下次检查均线位置与价格承接是否同时改变。")
    days = mi.sessions(analysis["calendar"], asof, 6)
    lookup = mi.price_lookup(analysis["prices"], asof)
    if len(days) >= 2:
        a, b = lookup.get((days[-2], symbol)), lookup.get((asof, symbol))
        if a is not None and b is not None:
            points.insert(0, f"最近一交易日复权收盘变化 {(b/a-1)*100:+.2f}%。")
    if len(days) == 6:
        a, b = lookup.get((days[0], symbol)), lookup.get((asof, symbol))
        if a is not None and b is not None:
            points.append(f"最近5个交易日复权收盘变化 {(b/a-1)*100:+.2f}%。")
    if not points:
        points.append("当前归档没有这只标的可用的连续价格或均线参考，先到技术页核对结构。")
    return list(dict.fromkeys(points))


def quick_follow(candidate, style, evidence, current, records):
    """Freeze system observations without pretending the user chose an entry."""
    duplicate = next((r for r in records.values() if r["plan"]["symbol"] == candidate["symbol"]
                     and r["plan"]["style"] == style and r["plan"]["asof"] == candidate["date"]
                     and r["outcome"]["status"] not in ("已结束", "已放弃")), None)
    if duplicate:
        return duplicate["id"], False
    book = PLAYBOOKS[style]
    record = engine.new_plan(dict(symbol=candidate["symbol"], name=candidate["name"], style=style,
        mode="模拟观察", asof=candidate["date"], data_current=current, thesis="；".join(candidate["reasons"]),
        trigger="关注记录，尚未设定个人观察条件。下次复盘核对：" + book["question"],
        invalid=book["invalid"], exit_rule="下一次复盘核对名单、风险标签和价格变化；尚未设定交易退出条件。",
        cost_risk="仅加入观察，尚未作出资金安排。", funding_check="待核对", strategy_check="研究假设",
        capacity="待确认", manual_evidence="", candidate_evidence=candidate,
        market_evidence=evidence, quick_follow=True), now=beijing_now().isoformat())
    records[record["id"]] = record
    return record["id"], True


def render_tracking(record, current, analysis, asof, excluded):
    delta = mi.focus_changes(record, current, analysis["prices"], asof)
    st.markdown("**当时为何关注**")
    st.write("；".join(delta["original_reasons"]))
    st.markdown("**现在有哪些变化**")
    if asof <= delta["baseline"]:
        st.info("尚无比关注日更新的数据，暂时没有后续变化可核对。")
    else:
        if delta["return_pct"] is not None:
            st.write(f"关注日 {delta['baseline']} 至 {asof}，复权收盘价变化 **{pct(delta['return_pct'])}**。这不是个人持仓收益。")
        else:
            st.caption("缺少关注日或本日价格，暂不计算价格变化。")
        if delta["new_sources"]:
            st.write("新增入选：" + "、".join(source_label(s) for s in delta["new_sources"]))
        if delta["lost_sources"] and not excluded:
            st.write("本日不再入选：" + "、".join(source_label(s) for s in delta["lost_sources"]))
        if excluded:
            st.caption("部分来源未更新，无法确认是否退出全部观察池。")
        if delta["new_risks"]:
            st.warning("新增风险：" + "；".join(delta["new_risks"]))
        if delta["rps_before"] is not None and delta["rps_now"] is not None:
            st.write(f"RPS50：{delta['rps_before']:.1f} → {delta['rps_now']:.1f}"
                     f"（变化 {delta['rps_now']-delta['rps_before']:+.1f} 个点）。")
        if current and not delta["new_sources"] and not delta["lost_sources"] and not delta["new_risks"]:
            st.write("观察池归属与已记录风险标签暂无变化。")
    st.markdown("**需要重新检查的条件**")
    st.write(record["plan"]["invalid"])
    st.caption("条件原文保留供逐项核对；系统没有将文字条件自动判为已触发。")

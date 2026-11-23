"""Visible filtering and side-by-side research; no unexplained ranking score."""
from __future__ import annotations

import hashlib

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import candidate_compare as compare
import investor_journey as journey
import research_store as store
import research_workspace as workspace
import workbench_insights_ui as insights
from market_insights import price_lookup, sessions
from site_data import load_csv, load_json
from stock_search import stock_label

COLUMNS = {"行业": "industry", "最近收盘": "close", "RPS50": "rps", "RPS50变化": "rps_delta",
           "1日涨跌幅(%)": "return1", "5日涨跌幅(%)": "return5", "20日涨跌幅(%)": "return20",
           "换手率(%)": "turnover", "量比": "volume_ratio", "连续在强势榜天数": "streak",
           "强势榜首次入选日": "first_seen", "名单变化": "membership"}


def history_for(analysis, asof):
    return {date: load_json(f"data/candidates/history/{date}.json")
            for date in sessions(analysis.get("calendar", {}), asof, 6)}


def comparison(selected, rows, analysis, asof):
    options = {r["symbol"]: r for r in rows}
    selected = [code for code in selected if code in options]
    if not selected:
        return
    fields = {"行业": "industry", "收盘价": "close", "RPS50": "rps", "5日涨跌幅(%)": "return5",
              "20日涨跌幅(%)": "return20", "换手率(%)": "turnover", "名单变化": "membership"}
    table = {f"{options[c]['name']} {c}": {label: options[c].get(field) for label, field in fields.items()}
             | {"入选依据": "；".join(options[c]["reasons"]),
                "已记录风险": "；".join(options[c]["risks"]) or "未记录，需查公告与交易限制"} for c in selected}
    formatted = {name:{label:("—" if value is None else f"{value:.2f}" if isinstance(value,(float,int)) else str(value))
                      for label,value in fields.items()} for name,fields in table.items()}
    st.dataframe(pd.DataFrame(formatted), width="stretch")
    days = sessions(analysis.get("calendar", {}), asof, 21)
    lookup = price_lookup(analysis.get("prices"), asof)
    common = [d for d in days if all((d,c) in lookup for c in selected)]
    if len(common) >= 2:
        start = common[0]
        fig = go.Figure()
        for code in selected:
            dates = [d for d in days if d >= start]
            values = [(lookup[d,code]/lookup[start,code]-1)*100 if (d,code) in lookup else None for d in dates]
            fig.add_scatter(x=pd.to_datetime(dates), y=values, mode="lines", connectgaps=False, name=options[code]["name"])
        fig.update_layout(height=280, margin=dict(t=15,b=10,l=10,r=10), yaxis_title="相对共同起点涨跌幅 (%)", legend_orientation="h")
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"共同起点 {start}，使用复权收盘价比较变化；缺价留断点。下单和个人价位核对使用实际报价。")
    else:
        st.caption("缺少共同日期的历史价格，暂不绘制对照曲线。")


def render(rows, excluded, book, asof, scope, style, analysis, evidence, current, records, frames):
    if style == "fundamental":
        st.subheader("基本面研究")
        st.info("财报与估值筛选数据尚未接入。可手动建立研究档案，记录公告、经营假设、估值条件与下次核验日期。")
        if st.button("建立基本面研究档案 →", key=scope+"_fundamental"):
            st.session_state[store.key()+"_new_style"] = "fundamental"
            journey.go(journey.WORKBENCH_PAGE)
        for label, page in book["pages"]:
            journey.page_link(label+" →", page, key=scope+label)
        return
    for message in excluded:
        st.info(message)
    if not rows:
        st.info("这份同日名单没有可展示的候选。" + book["automatic"])
        return
    history = history_for(analysis, asof)
    rows, quotes, previous = compare.enrich(rows, frames, load_csv("data/market_snapshot.csv"), analysis, history, asof)
    for row in rows:
        row["membership"] = compare.membership(row["symbol"], style, history, previous)
    st.caption("按可见字段筛选与排序；多来源重合仅表示同时命中规则。")
    query_key, industries_key = scope+"_query", scope+"_industries"
    a, b, c = st.columns([2,2,1])
    query = a.text_input("查找股票或行业", key=query_key, placeholder="名称、代码、拼音首字母或行业")
    industries = b.multiselect("限定研究行业（留空查看全部）", sorted({r["industry"] for r in rows if r["industry"]}), key=industries_key, placeholder="全部行业")
    sorting = c.selectbox("排序依据", ("代码", "RPS50", "5日涨跌幅", "20日涨跌幅", "换手率", "连续在强势榜天数"), key=scope+"_sort")
    with st.expander("更多筛选与显示列"):
        a,b = st.columns(2)
        membership = a.selectbox("名单变化", ("全部", "较上一交易日新增", "延续入选", "历史待积累"), key=scope+"_membership")
        ceiling = b.number_input("5日涨幅上限 (%)，留空不限", value=None, step=1.0, key=scope+"_ceiling")
        clean = a.checkbox("仅展示没有已记录风险标签的候选", key=scope+"_clean")
        intersection = b.checkbox("仅看至少两个来源同时入选", key=scope+"_intersection")
        ascending = st.checkbox("数值从低到高排列", key=scope+"_ascending")
        columns = st.multiselect("显示列", list(COLUMNS), default=["行业", "最近收盘", "RPS50", "5日涨跌幅(%)", "名单变化"], key=scope+"_columns")
        st.caption("无风险标签表示尚未记录风险；历史不足的涨幅留空，启用涨幅上限时不纳入。代码排序仅便于查找。")
    filtered = compare.select(rows, query=query, industries=industries, membership_filter=membership,
                              only_clean=clean, intersection=intersection, max_return5=ceiling, sort=sorting, ascending=ascending)
    if not filtered:
        st.info("当前筛选没有结果，可清除条件或继续观察已有标的。")
        def clear():
            for suffix in ("_query", "_industries", "_clean", "_intersection", "_membership", "_ceiling"):
                st.session_state.pop(scope+suffix, None)
        st.button("清除筛选并恢复全部候选", key=scope+"_clear_filters", on_click=clear)
        return
    st.write(f"**当前 {len(filtered)} 只** · 点击表格行继续研究，也可选择多只并列比较。")
    frame = pd.DataFrame([{"代码": r["symbol"], "名称": r["name"], **{label:r.get(COLUMNS[label]) for label in columns},
                           "入选依据":"；".join(r["reasons"])} for r in filtered])
    identity = hashlib.sha256("|".join(r["symbol"] for r in filtered).encode()).hexdigest()[:10]
    event = st.dataframe(frame, hide_index=True, width="stretch", height=360,
                         on_select="rerun", selection_mode="single-row", key=scope+"_table_"+identity,
                         column_config={label:st.column_config.NumberColumn(label,format="%.2f") for label in columns
                                        if COLUMNS[label] not in ("industry", "membership", "first_seen")})
    selected_rows = event.selection.rows
    options = {r["symbol"]: r for r in filtered}
    selection_key = scope+"_last_row_"+identity
    if selected_rows and selected_rows[0] < len(filtered) and st.session_state.get(selection_key) != selected_rows:
        st.session_state[scope+"_candidate"] = filtered[selected_rows[0]]["symbol"]
    st.session_state[selection_key] = list(selected_rows)
    if st.session_state.get(scope+"_candidate") not in options:
        st.session_state.pop(scope+"_candidate", None)
    picked = st.multiselect("并列比较（最多四只）", list(options), format_func=lambda code:stock_label(options[code]['name'], code),
                            max_selections=4, key=scope+"_compare_"+identity, placeholder="输入名称、代码或拼音首字母，选择要比较的股票")
    comparison(picked, filtered, analysis, asof)
    selected = st.selectbox("选择要继续研究的标的", list(options), index=None,
                             format_func=lambda code:stock_label(options[code]['name'], code), key=scope+"_candidate", placeholder="输入名称、代码或拼音首字母")
    if selected is None:
        return
    candidate = options[selected]
    with st.container(border=True):
        st.markdown(f"**{candidate['name']} · {selected}**")
        st.write("入选依据：" + "；".join(candidate["reasons"]))
        for point in insights.candidate_detail(candidate, style, analysis, asof):
            st.write(point)
        if candidate["risks"]:
            st.warning("；".join(candidate["risks"]))
        st.caption("需要核对：" + book["manual"])
        existing = workspace.active_by_symbol(list(records.values())).get(selected, [])
        if existing:
            st.info(f"这只股票已有 {len(existing)} 条研究记录，可以接着原有判断核对。")
        left, middle, right = st.columns(3)
        if left.button("查看这只标的的技术图 →", key=scope+"_chart", width="stretch"):
            journey.go(journey.QUOTE_PAGE, selected)
        if middle.button("加入观察 · 自动保留依据", key=scope+"_follow", type="primary", width="stretch"):
            saved, added, err = store.follow(candidate, style, evidence, current and not excluded)
            if err:
                st.error(store.error_text(err))
            else:
                st.success(("已加入观察。" if added else "已保留原有观察记录。") + store.save_message(saved))
        if right.button("打开研究档案 →", key=scope+"_tracking", width="stretch"):
            saved, _, err = store.follow(candidate, style, evidence, current and not excluded)
            st.session_state[store.key()+"_selected_record"] = saved["id"]
            journey.go(journey.WORKBENCH_PAGE, selected)
    st.caption("“新增/延续”仅在上一交易日完整名单可用时显示。完整归档从本版开始积累，早期前60名归档不用于断言新增或退出。")

"""A daily desk driven by the user's research, deadlines and own price conditions."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import candidate_compare
import database
import decision_workbench as engine
import investor_journey as journey
import market_insights as mi
import research_store as store
import research_ui
import research_workspace as workspace
from site_data import load_csv
from strategy_playbooks import JOURNAL_STYLES as PLAYBOOKS, PLAYBOOKS as A_SHARE_STYLES
from stock_search import stock_label
from update_schedule import beijing_now


def _quotes(frames, asof):
    return candidate_compare.raw_quotes(frames, load_csv("data/market_snapshot.csv"), asof)


def new_research(asof, *, key):
    scope = store.key()
    selected_style = st.session_state.pop(scope+"_new_style", None)
    style_key = key+"_style"
    if selected_style in A_SHARE_STYLES:
        st.session_state[style_key] = selected_style
    with st.form(key):
        a, b = st.columns(2)
        code = a.text_input("证券代码（含交易所）", placeholder="600519.SH / 159915.SZ")
        name = b.text_input("名称")
        style = st.selectbox("研究方式", list(A_SHARE_STYLES), key=style_key, format_func=lambda k:PLAYBOOKS[k]["name"])
        reason = st.text_input("这次为什么关注", placeholder="写下一条实际依据，之后可以继续补充")
        evidence_date = st.date_input("这条依据的日期", value=beijing_now().date(), max_value=beijing_now().date())
        submitted = st.form_submit_button("建立研究档案", type="primary")
    if submitted:
        try:
            candidate = dict(symbol=code, name=name, industry="", date=evidence_date.strftime("%Y%m%d"),
                             reasons=[reason.strip() or "手动关注，依据待补充"], sources=["手动研究"], risks=[])
            saved, _, err = store.follow(candidate, style)
            st.session_state[scope+"_selected_record"] = saved["id"]
            st.session_state[scope+"_flash"] = (bool(err), store.save_message(saved, err))
            journey.go(journey.WORKBENCH_PAGE, saved["plan"]["symbol"])
        except ValueError as exc:
            st.error(str(exc))


def _watchlist(quotes, records):
    uid = store.owner()
    if uid is None:
        return
    cache_key = store.key()+"_existing_watchlist"
    if st.session_state.get("user_watchlist_owner") == uid and "user_watchlist" in st.session_state:
        st.session_state[cache_key] = list(st.session_state["user_watchlist"])
    if cache_key not in st.session_state:
        try:
            st.session_state[cache_key] = database.get_user_watchlist(uid)
        except Exception:
            st.caption("暂时无法读取已有自选，研究记录仍可使用。")
            return
    symbols = [engine.symbol_id(c) for c in st.session_state[cache_key]]
    tracked = workspace.active_by_symbol(list(records.values()))
    symbols = list(dict.fromkeys(c for c in symbols if c and c not in tracked))
    if not symbols:
        return
    with st.expander(f"已有自选中，还有 {len(symbols)} 只未建立研究档案"):
        selected = st.selectbox("选择已有自选", symbols, format_func=lambda c:stock_label(quotes.get(c,{}).get('name',c), c),
                                help="可输入名称、代码或拼音首字母搜索。")
        if st.button("从这只自选开始研究", key=cache_key+"_adopt"):
            quote = quotes.get(selected) or {}
            candidate = dict(symbol=selected, name=quote.get("name") or selected, industry=quote.get("industry") or "",
                date=quote.get("date") or beijing_now().strftime("%Y%m%d"), reasons=["来自已有自选清单"], risks=[], sources=["个人自选"])
            saved, _, err = store.follow(candidate)
            st.session_state[store.key()+"_selected_record"] = saved["id"]
            journey.go(journey.WORKBENCH_PAGE, selected)


def _changes(record, current, excluded, quote, analysis, asof):
    if record["plan"]["style"] == "us_options":
        import datetime as dt
        from us_options_data import NY
        today = dt.datetime.now(NY).strftime("%Y%m%d")
        checks = workspace.price_checks(record, None, today=today)
        expiry = (record["plan"].get("market_evidence", {}).get("us_options") or {}).get("expiry", "")
        if expiry and expiry.replace("-", "") <= today:
            checks.append(dict(kind="expiry", text="期权已到期或进入到期日，请核对平仓、交割与指派"))
        return ["按原期权合约核对报价、退出条件与到期安排"], [r["text"] for r in checks], checks
    from workbench_insights_ui import source_label
    delta = mi.focus_changes(record, current, analysis.get("prices"), asof)
    changes = []
    if asof > record["plan"]["asof"]:
        if delta["new_risks"]:
            changes.append("新增风险："+"；".join(delta["new_risks"]))
        if delta["new_sources"]:
            changes.append("新增命中：" + "、".join(source_label(s) for s in delta["new_sources"]))
        lost = [s for s in delta["lost_sources"] if s.startswith("data/")]
        if lost and not excluded:
            changes.append("不再命中：" + "、".join(source_label(s) for s in lost))
        if delta["return_pct"] is not None:
            changes.append(f"原证据日 {record['plan']['asof']} 起复权收盘变化 {delta['return_pct']:+.2f}%")
    checks = workspace.price_checks(record, quote, today=beijing_now().strftime("%Y%m%d"))
    alerts = [r["text"] for r in checks if r["kind"] != "missing"]
    return changes, alerts, checks


def render_home(frames, evening, asof, publication, evidence, analysis):
    st.title("我的复盘")
    st.caption("关注自己的变化，保留当时的判断。市场、选股和研究档案都可以直接打开。")
    st.caption(f"收盘数据 {asof or '暂无'} · {publication.get('message','')}")
    store.adopt_legacy_drafts()
    records = store.load()
    research_ui.storage_status(records)
    quotes = _quotes(frames, asof)
    a,b,c = st.columns(3)
    with a:
        journey.page_link("市场全景 →", "🛸 全市场看板", key="home_market")
    with b:
        journey.page_link("筛选与比较 →", "🧭 多策略选股", key="home_candidates")
    with c:
        journey.page_link("全部研究档案 →", journey.WORKBENCH_PAGE, key="home_journal")
    groups = workspace.active_by_symbol(list(records.values()))
    active = [r for group in groups.values() for r in group if r["outcome"]["status"] not in ("已结束", "已放弃")]
    _watchlist(quotes, records)
    if not active:
        st.subheader("最近归档名单的后续表现")
        summary=[]
        for style, series in analysis.get("outcomes",{}).items():
            row=series[-1] if series else None
            if row:
                summary.append({"名单":PLAYBOOKS[style]["name"], "名单日期":row["selected_on"],
                    "核对日期":row["date"], "中位涨跌幅(%)":row["median"], "可核对/归档":f"{row['n']}/{row['base']}"})
        if summary:
            st.dataframe(pd.DataFrame(summary),hide_index=True,width="stretch",
                         column_config={"中位涨跌幅(%)":st.column_config.NumberColumn(format="%.2f")})
            st.caption("比较归档名单日收盘与后续收盘。历史强势榜最多留存前60只；这里展示名单表现，实际成交和成本尚未纳入。")
        else:
            st.info("暂缺可核对的历史名单，可以先浏览市场与候选。")
        st.subheader("先留下你正在关注的一只股票")
        st.write("添加关注后，下次回来会看到关注后的变化、复核日期和个人价位条件。")
        with st.expander("添加关注 / 手动研究"):
            new_research(asof, key=store.key()+"_home_new")
        return
    pools = {style: engine.candidate_rows(style, frames, evening, asof=asof) for style in {r["plan"]["style"] for r in active}}
    entries = []
    for record in active:
        plan = record["plan"]
        candidates, excluded = pools[plan["style"]]
        current = next((r for r in candidates if r["symbol"] == plan["symbol"]), None)
        quote = quotes.get(plan["symbol"])
        changes, alerts, checks = _changes(record, current, excluded, quote, analysis, asof)
        values = workspace.research(record)
        change_text = "；".join(changes) or ("暂无比关注日更新的变化" if asof <= plan["asof"] else "归档条件暂无变化")
        if excluded:
            change_text += "；部分来源待更新"
        entries.append(dict(id=record["id"], code=plan["symbol"], name=plan.get("name") or plan["symbol"],
            style=PLAYBOOKS[plan["style"]]["name"], mode=plan["mode"], status=record["outcome"]["status"],
            alerts="；".join(alerts), changes=change_text,
            due=values.get("review_on") or "未设置", close=quote.get("close") if quote else None,
            date=quote.get("date") if quote else "缺数据", thesis=values["thesis"],
            missing="；".join(r["text"] for r in checks if r["kind"] == "missing")))
    tabs = st.tabs(["关注变化", "持仓分布", "个人复盘"])
    with tabs[0]:
        st.subheader("哪些需要重新看一眼")
        alerted = sum(bool(r["alerts"]) for r in entries)
        st.write(f"关注 {len({r['plan']['symbol'] for r in active})} 只 · 活跃记录 {len(active)} 条 · 达到自定复核条件 {alerted} 条")
        only = st.checkbox("只看达到个人复核条件的记录", key=store.key()+"_attention_only")
        shown = [r for r in entries if not only or r["alerts"]]
        shown.sort(key=lambda r:(not bool(r["alerts"]), r["code"]))
        if not shown:
            st.info("当前没有记录达到已设置的收盘价位或复核日期。未设置条件、报价缺失和盘中变化仍需自行查看。")
        else:
            st.dataframe(pd.DataFrame([{"代码": r["code"], "名称": r["name"], "方式":r["style"],
                "需要复核":r["alerts"] or r["missing"] or "—", "关注后的变化":r["changes"],
                "下次复核":r["due"], "最近收盘":r["close"], "报价日期":r["date"]} for r in shown]),
                hide_index=True, width="stretch")
            options={r["id"]:r for r in shown}
            selected=st.selectbox("打开一条继续研究", list(options), index=None,
                format_func=lambda i:stock_label(options[i]['name'], options[i]['code'], f"{options[i]['style']} · {options[i]['mode']}"), key=store.key()+"_home_pick", placeholder="输入名称、代码或拼音首字母")
            if selected and st.button("查看变化、更新条件 →", key=store.key()+"_open", type="primary"):
                st.session_state[store.key()+"_selected_record"] = selected
                journey.go(journey.WORKBENCH_PAGE, options[selected]["code"])
        st.caption("价格检查发生在你打开页面并读到收盘数据时。复权涨跌幅用于回顾；个人价格条件使用未复权收盘价。")
        with st.expander("继续添加关注"):
            new_research(asof, key=store.key()+"_home_more")
    with tabs[1]:
        research_ui.position_table(records, quotes)
    with tabs[2]:
        render_personal_stats(records)


def render_personal_stats(records):
    st.subheader("自己的判断，后来怎样了")
    mode = st.radio("统计范围", engine.MODES, horizontal=True, key=store.key()+"_stats_mode")
    rows=[]
    for style, book in PLAYBOOKS.items():
        stats=engine.journal_stats(list(records.values()), mode=mode, style=style)
        if stats["total"]:
            rows.append({"研究方式":book["name"], "币种":stats["currency"], "已结束":stats["n"], "其他记录":stats["pending"],
                "盈利占比(%)":100*stats["win_rate"] if stats["win_rate"] is not None else None,
                "平均盈利/平均亏损":stats["payoff"], "单笔平均净盈亏（原币）":stats["expectancy"], "偏离计划次数":stats["breaks"]})
    if rows:
        st.dataframe(pd.DataFrame(rows),hide_index=True,width="stretch")
    else:
        st.info("当前没有这类个人记录。执行或放弃后回到研究档案，留下原因，再检查哪些判断反复出错。")
    st.caption("按自己填写的已结束记录统计，模拟与实盘、人民币与美元分别计算；结果依赖记录完整性，未核验券商成交。")


def render_tracking(frames, evening, asof, analysis):
    store.adopt_legacy_drafts()
    records=store.load()
    research_ui.storage_status(records)
    quotes=_quotes(frames, asof)
    _watchlist(quotes,records)
    with st.expander("新建研究档案", expanded=not records):
        journey.page_link("建立美股期权预案 →", "🇺🇸 美股期权策略", key="journal_new_options")
        new_research(asof,key=store.key()+"_journal_new")
    if not records:
        return
    groups=workspace.active_by_symbol(list(records.values()))
    scope=store.key()
    preferred=st.session_state.pop(scope+"_selected_record",None)
    transferred=engine.symbol_id(st.session_state.pop("workbench_symbol",None))
    if preferred in records:
        transferred=records[preferred]["plan"]["symbol"]
        st.session_state[scope+"_version"]=preferred
    if transferred in groups:
        st.session_state[scope+"_stock"]=transferred
    selected=st.selectbox("研究的标的",list(groups),format_func=lambda code:stock_label(groups[code][0]['plan'].get('name'), code),key=scope+"_stock",
                          help="可输入名称、代码或拼音首字母搜索。")
    versions={r["id"]:r for r in groups[selected]}
    if st.session_state.get(scope+"_version") not in versions:
        st.session_state.pop(scope+"_version",None)
    ident=st.selectbox("这个标的的研究记录",list(versions),key=scope+"_version",
        format_func=lambda i:f"{PLAYBOOKS[versions[i]['plan']['style']]['name']} · {versions[i]['plan']['mode']} · {versions[i]['plan']['asof']} · {versions[i]['outcome']['status']}")
    record=versions[ident]
    tracking=engine.tracking_evidence(record,frames,evening,asof=asof) if asof else {"current":None,"excluded":["尚无收盘数据"]}
    if asof:
        changes,alerts,checks=_changes(record,tracking["current"],tracking["excluded"],quotes.get(selected),analysis,asof)
        if changes:
            st.write("**关注后的变化：** "+"；".join(changes))
        if tracking["excluded"]:
            st.caption("部分候选来源不完整，名单退出情况暂不能确认。")
    research_ui.detail(record,quotes.get(selected))

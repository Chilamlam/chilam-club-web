"""Evidence → style → candidate → frozen plan → personal feedback."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import auth
import decision_workbench as engine
import investor_journey as journey
import journal_store
import review_language
import workbench_insights_ui as insights
from data_freshness import norm_date
from publication_status import publication_status
from site_data import load_csv, load_json
from strategy_playbooks import PLAYBOOKS
from update_schedule import beijing_now

def _context():
    frames = {k: load_csv(p) for k, p in (("strong", "data/strong_stocks.csv"),
              ("breakout", "data/breakout_stocks.csv"), ("etf", "data/strong_etfs.csv"))}
    sentiment = load_json("data/sentiment/derived.json")
    evening = review_language.current_wording(load_json("data/evening_plan/latest.json"))
    market = load_csv("data/market_sentiment.csv")
    labels = {"strong": "个股强度榜", "breakout": "新高突破池", "etf": "ETF强度榜"}
    dates = {"短线情绪": sentiment.get("date"), "晚间战法池": evening.get("date"),
             **{labels[k]: engine.snapshot_date(v) for k, v in frames.items()}}
    if not market.empty and "date" in market:
        dates["市场成交"] = max(market["date"].map(norm_date), default="")
    now = beijing_now()
    today = now.strftime("%Y%m%d")
    asof = max((norm_date(d) for d in dates.values() if norm_date(d) and norm_date(d) <= today), default="")
    publication = publication_status(dates, now=now, calendar=load_json("data/trading_calendar.json"))
    evidence = engine.market_evidence(sentiment, market, evening, asof=asof) if asof else {"facts": [], "warnings": [], "unavailable": "暂无证据"}
    return frames, evening, asof, publication, evidence


def _store_key():
    return f"decision_journal_{auth.get_user_id() if auth.is_logged_in() else 'guest'}"


def _storage_error(err):
    if err.get("code") in ("42P01", "PGRST205"):
        return "账号记录表尚未初始化；当前内容仍在本次会话，请先下载备份。管理员可按部署说明启用云端保存。"
    return "账号同步失败，本次会话内容仍保留：" + str(err.get("message") or "未知错误")[:250]


def _clear_candidate_filter_state(*keys: str) -> None:
    for key in keys:
        st.session_state.pop(key, None)


def _render_candidates(rows, excluded, book, asof, scope, style, analysis, evidence, current, records):
    st.subheader(book["name"] + "候选")
    st.caption("入选只表示命中已归档规则；多个来源重合不自动增加胜率。")
    for message in excluded:
        st.info(message)
    if not rows:
        st.info("当前没有可合并的候选。" + book["automatic"])
        for label, page in book["pages"]:
            journey.page_link(label + " →", page, key="dw_empty_" + label)
        return None
    query_key = f"{scope}_query"
    clean_key = f"{scope}_clean"
    intersection_key = f"{scope}_intersection"
    industries_key = f"{scope}_industries"
    industries = sorted({r["industry"] for r in rows if r["industry"]})
    with st.popover(f"⚙️ 筛选 {len(rows)} 只候选", width="content"):
        query = st.text_input("查找代码、名称或行业", key=query_key).strip().lower()
        only_clean = st.checkbox("仅展示没有已记录风险标签的候选", key=clean_key)
        intersection = st.checkbox("仅看至少两个来源同时入选", key=intersection_key)
        selected_industries = st.multiselect("限定研究行业（留空查看全部）", industries, key=industries_key)
        st.caption("没有风险标签表示现有规则未记录风险，不代表已完成排雷。")
    filtered = [r for r in rows if (not query or query in (r["symbol"] + r["name"] + r["industry"]).lower())
                and (not only_clean or not r["risks"])
                and (not intersection or len(r["sources"]) >= 2)
                and (not selected_industries or r["industry"] in selected_industries)]
    st.write(f"证据日期 {asof} · 合并去重 {len(rows)} 只 · 当前显示 {len(filtered)} 只")
    if not filtered:
        conditions = []
        if query:
            conditions.append(f"关键词“{query}”")
        if only_clean:
            conditions.append("无已记录风险标签")
        if intersection:
            conditions.append("至少两个来源")
        if selected_industries:
            conditions.append("行业：" + "、".join(selected_industries))
        st.warning("当前筛选没有结果。" + ("已启用：" + "；".join(conditions) + "。" if conditions else ""))
        st.button(
            "清除筛选并恢复全部候选",
            key=f"{scope}_clear_filters",
            on_click=_clear_candidate_filter_state,
            args=(query_key, clean_key, intersection_key, industries_key),
        )
        return None
    options = {r["symbol"]: r for r in filtered}
    selected = st.selectbox("选择要继续研究的标的", list(options),
        format_func=lambda s: f"{options[s]['name']} · {s}", key=f"{scope}_candidate")
    candidate = options[selected]
    with st.container(border=True):
        st.markdown(f"**{candidate['name']} · {candidate['symbol']}**")
        left, middle, right = st.columns(3)
        if left.button("查看这只标的的技术图 →", key=f"{scope}_chart"):
            journey.go(journey.QUOTE_PAGE, selected)
        if middle.button("加入观察 · 自动保留依据", key=f"{scope}_follow", type="primary"):
            _, added = insights.quick_follow(candidate, style, evidence, current and not excluded, records)
            st.success("已加入本次会话的观察记录，打开“观察记录”查看。可下载备份或登录后同步。" if added else "这只标的已有同日同风格观察记录，可在“观察记录”查看。")
        if right.button("打开观察记录 →", key=f"{scope}_tracking"):
            journey.go(journey.WORKBENCH_PAGE)
        st.markdown("**为何入选：** " + "；".join(candidate["reasons"]))
        for point in insights.candidate_detail(candidate, style, analysis, asof):
            st.write(point)
        st.markdown("**需要警惕：** " + book["invalid"])
        if candidate["risks"]:
            st.warning("；".join(candidate["risks"]))
        st.caption("观察来源：" + "、".join(insights.source_label(s) for s in candidate["sources"]))
        st.caption("加入观察后自动对照后续入池变化、风险标签和收盘价格；无需先填写交易计划。")
    with st.expander(f"查看当前筛选的 {len(filtered)} 只候选明细", expanded=False):
        st.caption("完整名单用于逐只核对；改变上方选择可立即更新当前研究标的。")
        st.dataframe(pd.DataFrame([{"代码": r["symbol"], "名称": r["name"], "行业": r["industry"],
            "入选依据": "；".join(r["reasons"]), "已记录风险": "；".join(r["risks"]) or "未记录，仍需核对"}
            for r in filtered]), hide_index=True, use_container_width=True)
    return candidate


def _render_new_plan(candidate, style, asof, current, evidence, records, scope):
    book = PLAYBOOKS[style]
    st.subheader("补充自己的交易条件")
    st.caption("保存后保留原始风格、依据和条件；后续只更新执行结果。改变观点时另建计划。")
    if candidate and st.checkbox("手动建立其他标的计划", key=f"{scope}_{style}_manual_plan"):
        candidate = None
    ident = candidate["symbol"] if candidate else "manual"
    key = f"{scope}_{style}_{ident}_{asof}"
    with st.form(f"{key}_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        symbol = c1.text_input("证券代码（含交易所）", value=candidate["symbol"] if candidate else "",
                               placeholder="600519.SH / 159915.SZ", key=f"{key}_symbol", disabled=bool(candidate))
        name = c2.text_input("名称", value=candidate["name"] if candidate else "", key=f"{key}_name")
        mode = st.radio("记录类型", engine.MODES, horizontal=True, key=f"{key}_mode")
        thesis = st.text_area("观察依据", value="；".join(candidate["reasons"]) if candidate else "",
                             placeholder=book["question"], key=f"{key}_thesis")
        trigger = st.text_area("触发条件", placeholder=book["trigger"], key=f"{key}_trigger", height=80)
        invalid = st.text_area("失效条件", placeholder=book["invalid"], key=f"{key}_invalid", height=80)
        exit_rule = st.text_input("退出与期限", placeholder="价格、逻辑或时间退出；下一次何时检查", key=f"{key}_exit")
        cost_risk = st.text_input("成本与风险预算", placeholder="手续费、滑点、跳空和同类持仓集中度；自己可承受的损失", key=f"{key}_risk")
        c1, c2, c3 = st.columns(3)
        funding = c1.selectbox("资金与承接前提", ("待核对", "已核对", "已出现反证"), key=f"{key}_funding")
        strategy = c2.selectbox("自己的策略验证", ("研究假设", "有独立记录"), key=f"{key}_strategy")
        capacity = c3.selectbox("时间与精力", ("待确认", "能按计划检查", "无法按计划检查"), key=f"{key}_capacity")
        manual = st.text_area("人工核对的来源、日期与反证", placeholder="将事实、解释和假设分开；核对过什么，什么变化会推翻判断", key=f"{key}_manual")
        do_save = st.form_submit_button("保存观察计划", type="primary")
    if do_save:
        try:
            record = engine.new_plan(dict(symbol=symbol, name=name, style=style, mode=mode,
                asof=asof, data_current=current, thesis=thesis, trigger=trigger, invalid=invalid,
                exit_rule=exit_rule, cost_risk=cost_risk, funding_check=funding, strategy_check=strategy,
                capacity=capacity, manual_evidence=manual, market_evidence=evidence,
                candidate_evidence=candidate), now=beijing_now().isoformat())
            records[record["id"]] = record
            st.session_state[f"{scope}_message"] = "已保存到本次会话。原始判断已冻结，可下载备份或同步到账号。"
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def _merge_cloud(records):
    remote, err = journal_store.load_records()
    if err:
        st.error(_storage_error(err))
        return
    conflicts = 0
    for record in remote:
        local = records.get(record["id"])
        if local and (local.get("_dirty") or not local.get("_synced")):
            conflicts += 1
            continue
        record.update(_synced=True, _dirty=False)
        records[record["id"]] = record
    st.success(f"已读取 {len(remote)} 条云端记录。")
    if conflicts:
        st.warning(f"{conflicts} 条本地待保存记录保留，未被云端覆盖；可先导出备份，再开启新会话读取云端版本。")


def _render_journal(records, scope, frames, evening, asof, analysis):
    st.caption("保留关注时的依据，对照后续价格、入池与风险标签变化。")
    message = st.session_state.pop(f"{scope}_message", "")
    if message:
        st.success(message)
    st.caption("游客记录仅保存在本次会话，请下载备份；登录后可同步到账号。")
    if auth.is_logged_in():
        if st.button("读取账号已有记录", key=f"{scope}_load"):
            _merge_cloud(records)
    else:
        st.info("游客可记录并下载；登录后可使用账号同步。")
    with st.expander("备份与恢复"):
        st.download_button("下载全部记录（JSON）", engine.export_journal(list(records.values())),
                           "decision-journal.json", "application/json", key=f"{scope}_export")
        upload = st.file_uploader("恢复之前下载的 JSON", type="json", key=f"{scope}_upload")
        if st.button("导入到本次会话", key=f"{scope}_import", disabled=upload is None):
            try:
                incoming = engine.import_journal(upload.getvalue())
                added = 0
                for record in incoming:
                    if record["id"] not in records:
                        records[record["id"]] = record
                        added += 1
                st.session_state[f"{scope}_message"] = f"已导入 {added} 条；同 ID 记录保留原有版本。导入内容来自文件，未验证成交真实性。"
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if not records:
        st.info("这里会保留关注当时的依据，并对照后续价格、候选池归属和风险标签变化。")
        c1, c2 = st.columns(2)
        if c1.button("去多策略选股 →", key=f"{scope}_empty_candidates", width="stretch"):
            journey.go("🧭 多策略选股")
        if c2.button("从技术图记录观察 →", key=f"{scope}_empty_quote", width="stretch"):
            journey.go(journey.QUOTE_PAGE)
        return
    labels = {k: f"{r['plan'].get('name') or r['plan']['symbol']} · "
                 f"{PLAYBOOKS[r['plan']['style']]['name']} · {r['plan']['asof']} · "
                 f"{r['outcome']['status']} · {k[:8]}" for k, r in records.items()}
    selected = st.selectbox("选择跟踪记录", list(reversed(records)), format_func=labels.get, key=f"{scope}_record")
    record = records[selected]
    outcome = record["outcome"]
    if asof:
        tracking = engine.tracking_evidence(record, frames, evening, asof=asof)
        insights.render_tracking(record, tracking["current"], analysis, asof, tracking["excluded"])
        with st.expander("核对跟踪来源完整性"):
            st.write("；".join(tracking["excluded"]) or "该风格所需候选来源日期一致。")
            st.caption("本页不监控盘中价格；触发、退出及是否成交由你核对后记录。")
    with st.expander("查看原计划与当时证据", expanded=False):
        st.text(engine.plan_markdown(record))
        for issue in record["plan"].get("checks", []):
            st.caption(issue)
        st.json(record["plan"].get("market_evidence") or {}, expanded=False)
    key = f"{scope}_{selected}"
    with st.expander("记录执行结果 / 结束观察"), st.form(f"{key}_outcome"):
        status = st.selectbox("跟踪状态", engine.STATUSES, index=engine.STATUSES.index(outcome["status"]))
        discipline_options = ("待复核", "按计划", "偏离计划")
        discipline = st.selectbox("执行质量", discipline_options,
                                 index=discipline_options.index(outcome.get("discipline", "待复核")))
        notes = st.text_area("实际发生了什么 / 偏差原因 / 下次只改哪条规则", value=outcome.get("notes", ""))
        cols = st.columns(3)
        gross = cols[0].number_input("毛盈亏（元，结束时填写）", value=outcome.get("gross_pnl"), step=100.0)
        costs = cols[1].number_input("总成本（元，含手续费与滑点）", min_value=0.0, value=outcome.get("costs"), step=1.0)
        closed = cols[2].date_input("结束日期", value=dt.datetime.strptime(norm_date(outcome["closed_on"]), "%Y%m%d").date() if outcome.get("closed_on") else None,
                                    max_value=beijing_now().date())
        submitted = st.form_submit_button("保存跟踪结果")
    if submitted:
        try:
            updated = engine.with_outcome(record, dict(status=status, discipline=discipline, notes=notes,
                gross_pnl=gross, costs=costs, closed_on=closed.isoformat() if closed else "",
                updated_at=beijing_now().isoformat()))
            updated["_dirty"] = True
            records[selected] = record = updated
            st.session_state[f"{scope}_message"] = "跟踪结果已保存在本次会话，原计划保持可追溯。"
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    c1, c2, c3 = st.columns(3)
    if c1.button("同步本条到账号", key=f"{key}_sync", disabled=not auth.is_logged_in()):
        saved, err = journal_store.save_record(record)
        if err:
            st.error(_storage_error(err))
        else:
            records[selected] = record = saved
            st.success("本条记录已保存到账号。")
    c2.download_button("下载本条计划", engine.plan_markdown(record), "observation-plan.md", "text/markdown", key=f"{key}_md")
    if c3.button("带入复盘答卷 →", key=f"{key}_review"):
        st.session_state["observation_review_text"] = {"owner": journey._plans_key(), "text": engine.plan_markdown(record)}
        journey.go(journey.REVIEW_PAGE)
    st.caption("当前记录：" + ("账号已同步" if record.get("_synced") and not record.get("_dirty") else "有会话内容待下载或同步"))
    st.markdown("**按原始风格统计自己的结果**")
    mode = st.radio("统计范围", engine.MODES, horizontal=True, key=f"{scope}_stats_mode")
    stats = []
    for style, book in PLAYBOOKS.items():
        result = engine.journal_stats(list(records.values()), mode=mode, style=style)
        if not result["total"]:
            continue
        stats.append({"原始风格": book["name"], "已结束样本": result["n"], "其他记录": result["pending"],
                      "胜率": f"{result['win_rate']:.1%}" if result["win_rate"] is not None else "—",
                      "盈亏比": f"{result['payoff']:.2f}" if result["payoff"] is not None else "—",
                      "单笔平均净盈亏(元)": result["expectancy"], "偏离计划次数": result["breaks"]})
    st.dataframe(pd.DataFrame(stats), hide_index=True, use_container_width=True)
    st.caption("仅统计本次已载入、已结束且成本完整的记录；净盈亏=毛盈亏−总成本。"
               "盈亏比=平均净盈利/平均净亏损绝对值，无盈利或亏损样本时留空。"
               "模拟与实盘分开；这是个人描述统计，不是策略回测，未接入真实成交，也不能由少量样本证明优势。")


def render_decision_workbench_page(section="candidates"):
    titles = {"candidates": "多策略选股", "themes": "题材跟踪", "outcomes": "名单表现", "tracking": "观察记录"}
    st.title(titles[section])
    frames, evening, asof, publication, evidence = _context()
    st.caption(f"数据日期 {asof or '未知'}")
    current = bool(asof and asof == publication["expected_date"] and publication["status"] in ("current", "scheduled"))
    if not current:
        st.caption(publication["message"])
    owner_key = _store_key()
    records = st.session_state.setdefault(owner_key, {})
    analysis = insights.load_insights(asof)
    if section == "themes":
        insights.render_themes(analysis, asof)
        return
    if section == "outcomes":
        insights.render_outcomes(analysis)
        return
    if section == "tracking":
        _render_journal(records, owner_key, frames, evening, asof, analysis)
        if asof:
            with st.expander("新建详细计划"):
                style = st.selectbox("计划风格", list(PLAYBOOKS), format_func=lambda k: PLAYBOOKS[k]["name"], key=f"{owner_key}_plan_style")
                transferred = engine.symbol_id(st.session_state.get("workbench_symbol"))
                prefill = st.session_state.get("workbench_prefill")
                if (
                    transferred
                    and isinstance(prefill, dict)
                    and engine.symbol_id(prefill.get("symbol")) == transferred
                ):
                    candidate = dict(
                        symbol=transferred,
                        name=str(prefill.get("name") or ""),
                        industry=str(prefill.get("industry") or ""),
                        reasons=[str(value) for value in (prefill.get("reasons") or [])],
                        risks=[str(value) for value in (prefill.get("risks") or [])],
                        sources=[str(value) for value in (prefill.get("sources") or [])]
                        or ["跨页观察记录"],
                    )
                else:
                    candidate = dict(
                        symbol=transferred,
                        name="",
                        reasons=[],
                        risks=[],
                        sources=["技术页"],
                    ) if transferred else None
                _render_new_plan(candidate, style, asof, current, evidence, records, owner_key)
        return
    style_keys = list(PLAYBOOKS)
    preview = {key: engine.candidate_rows(key, frames, evening, asof=asof)[0] if asof else [] for key in style_keys}
    style = st.segmented_control(
        "候选类型",
        style_keys,
        selection_mode="single",
        default=style_keys[0],
        format_func=lambda key: f"{PLAYBOOKS[key]['name']} · {len(preview[key])}只",
        key=f"{owner_key}_style",
    )
    if style is None:
        style = style_keys[0]
    scope = f"{owner_key}_{style}"
    rows, excluded = engine.candidate_rows(style, frames, evening, asof=asof) if asof else ([], ["缺少可用日期。"])
    _render_candidates(rows, excluded, PLAYBOOKS[style], asof, scope, style, analysis, evidence, current, records)


if __name__ == "__main__":
    st.set_page_config(page_title="Chilam Club · 复盘工作台", layout="wide")
    render_decision_workbench_page()

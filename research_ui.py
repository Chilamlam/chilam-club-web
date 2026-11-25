"""Shared research editor, storage feedback and position views."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import decision_workbench as engine
import journal_store
import investor_journey as journey
import research_store as store
import research_workspace as workspace
from strategy_playbooks import PLAYBOOKS
from update_schedule import beijing_now


def date_value(value):
    value = engine.norm_date(value)
    return dt.datetime.strptime(value, "%Y%m%d").date() if value else None


def storage_status(records):
    scope = store.key()
    flash = st.session_state.pop(scope + "_flash", None)
    if flash:
        (st.error if flash[0] else st.success)(flash[1])
    if store.owner() is None:
        st.caption("游客内容保存在本次会话；请下载备份。登录后，新保存的研究会自动存入账号。")
    else:
        guest_records = st.session_state.get("decision_journal_guest") or {}
        copied = {r["plan"].get("guest_source_id") for r in records.values()}
        pending_guest = sum(ident not in copied for ident in guest_records)
        if pending_guest:
            st.info(f"本次浏览中有 {pending_guest} 条游客研究，可以带入当前账号继续使用。")
            if st.button("把本次游客研究复制到账号", key=scope+"_copy_guest"):
                count, failed = store.copy_guest_records()
                st.session_state[scope+"_flash"] = (bool(failed), f"已带入 {count} 条研究。" + ("部分尚未保存，请重试或下载备份。" if failed else "已保存到当前账号。"))
                st.rerun()
        dirty = [r for r in records.values() if r.get("_dirty")]
        err = st.session_state.get(scope + "_load_error")
        if dirty or err:
            st.warning(f"有 {len(dirty)} 条修改待保存。" + (store.error_text(err) if err else "本窗口仍保留全部内容。"))
            if st.button("重试读取与保存", key=scope + "_retry"):
                store.load(force=True)
                for record in dirty:
                    store.save(record)
                st.rerun()
        else:
            st.caption("账号记录已自动读取；保存修改时会自动同步。")
    if records:
        st.download_button("下载全部研究备份", engine.export_journal(list(records.values())),
                           "chilam-research.json", "application/json", key=scope + "_backup")
    with st.expander("恢复备份"):
        upload = st.file_uploader("选择研究备份 JSON", type="json", key=scope + "_restore_file")
        if st.button("导入备份", key=scope + "_restore", disabled=upload is None):
            try:
                restored = engine.import_journal(upload.getvalue())
                count = 0
                for record in restored:
                    if record["id"] not in records:
                        record["_dirty"] = True
                        records[record["id"]] = record
                        count += 1
                st.success(f"已导入 {count} 条到本次会话，同 ID 的已有记录保留。")
                if store.owner() is not None and count:
                    st.info("核对后点击“重试读取与保存”，将导入的记录存入账号。")
            except ValueError as exc:
                st.error(str(exc))


def _saved(record, err):
    st.session_state[store.key() + "_flash"] = (bool(err), store.save_message(record, err))
    st.rerun()


def editor(record, *, prefix="research"):
    values = workspace.research(record)
    ident = f"{store.key()}_{prefix}_{record['id']}"
    # Versioned widget keys prevent values from a different revision being shown.
    revision = len(record.get("outcome", {}).get("research_history", []))
    with st.form(f"{ident}_{revision}_edit"):
        st.markdown("**自己的判断与条件**")
        thesis = st.text_area("为什么继续关注", value=values["thesis"], height=90,
                              help="写明你认可的事实及来源，例如公告日期、行业变化或价格结构。")
        left, right = st.columns(2)
        trigger = left.text_area("下一步要等什么", value=values["trigger"], height=90,
                                 placeholder="写出可检查的事件、价位或时间，例如：下一份公告确认订单交付。")
        invalid = right.text_area("什么变化会推翻判断", value=values["invalid"], height=90,
                                  placeholder="例如：订单取消；收盘跌破自己标记的参考低点；观察期限结束仍未确认。")
        exit_rule = st.text_input("退出或停止观察的条件", value=values["exit_rule"])
        manual = st.text_area("新证据与反证（附日期或来源）", value=values["manual_evidence"], height=80)
        cost_risk = st.text_input("可承受损失与执行限制", value=values["cost_risk"],
                                 help="记录自己能承担的损失、盯盘时间以及成交限制。")
        due = st.date_input("下次复核日", value=date_value(values.get("review_on")))
        with st.expander("设置价格复核条件（可留空）"):
            st.caption("使用未复权价格，在收盘数据更新后对照。除权除息后请重新核对价位；达到价位时仍由你判断是否行动。")
            c1, c2 = st.columns(2)
            low = c1.number_input("观察区间下沿", min_value=0.0, value=values.get("observe_low"), step=0.01, format="%.3f")
            high = c2.number_input("观察区间上沿", min_value=0.0, value=values.get("observe_high"), step=0.01, format="%.3f")
            below = c1.number_input("收盘不高于此价时复核", min_value=0.0, value=values.get("invalid_below"), step=0.01, format="%.3f")
            above = c2.number_input("收盘不低于此价时复核", min_value=0.0, value=values.get("invalid_above"), step=0.01, format="%.3f")
            basis = st.date_input("价位适用日期", value=date_value(values.get("price_basis_date")) or beijing_now().date())
        shares, cost, position_date = values.get("shares"), values.get("cost_price"), values.get("position_date")
        if record["plan"]["mode"] == "实盘记录":
            with st.expander("记录实际持仓（可留空）"):
                st.caption("填写当前持有数量与每股成本。数量填 0 表示已无持仓；分红、送转及增减仓后请更新。")
                a, b = st.columns(2)
                shares = a.number_input("当前持有数量", min_value=0.0, value=shares, step=100.0)
                cost = b.number_input("每股成本（元）", min_value=0.0, value=cost, step=0.01, format="%.3f")
                pos = st.date_input("持仓核对日期", value=date_value(position_date) or beijing_now().date(), max_value=beijing_now().date())
                position_date = pos.isoformat() if pos else ""
        reason = st.text_input("这次为什么修改", placeholder="例如：公告证伪原假设；缩短观察期限；实际减仓")
        submit = st.form_submit_button("保存研究与条件", type="primary")
    if submit:
        try:
            updated = workspace.revise(record, dict(thesis=thesis, trigger=trigger, invalid=invalid,
                exit_rule=exit_rule, cost_risk=cost_risk, manual_evidence=manual,
                observe_low=low, observe_high=high, invalid_below=below, invalid_above=above,
                review_on=due.isoformat() if due else "", price_basis_date=basis.isoformat() if basis else "",
                shares=shares, cost_price=cost, position_date=position_date),
                now=beijing_now().isoformat(), reason=reason)
            _saved(*store.save(updated))
        except ValueError as exc:
            st.error(str(exc))


def execution(record):
    outcome = record["outcome"]
    with st.form(f"{store.key()}_{record['id']}_execution"):
        status = st.selectbox("跟踪状态", engine.STATUSES, index=engine.STATUSES.index(outcome["status"]))
        options = ("待复核", "按计划", "偏离计划")
        discipline = st.selectbox("执行质量", options, index=options.index(outcome.get("discipline", "待复核")))
        notes = st.text_area("实际发生了什么 / 偏差原因 / 下次只改哪条规则", value=outcome.get("notes", ""))
        cols = st.columns(3)
        gross = cols[0].number_input("毛盈亏（元，结束时填写）", value=outcome.get("gross_pnl"), step=100.0)
        costs = cols[1].number_input("总成本（元，含手续费与滑点）", min_value=0.0, value=outcome.get("costs"), step=1.0)
        closed = cols[2].date_input("结束日期", value=date_value(outcome.get("closed_on")), max_value=beijing_now().date())
        st.caption("未参与的计划可标记“已放弃”；已结束的交易记录计入个人统计。")
        submitted = st.form_submit_button("保存跟踪结果")
    if submitted:
        try:
            updated = engine.with_outcome(record, dict(status=status, discipline=discipline, notes=notes,
                gross_pnl=gross, costs=costs, closed_on=closed.isoformat() if closed else "",
                updated_at=beijing_now().isoformat()))
            _saved(*store.save(updated))
        except ValueError as exc:
            st.error(str(exc))


def detail(record, quote=None):
    plan = record["plan"]
    st.subheader(f"{plan.get('name') or plan['symbol']} · {plan['symbol']}")
    st.caption(f"{PLAYBOOKS[plan['style']]['name']} · {plan['mode']} · 建立于 {record['created_at'][:10]} · 依据数据 {plan['asof']} · {record['outcome']['status']}")
    if quote:
        st.write(f"最近可用收盘 **{quote['close']:g} 元** · {quote['date']} · {quote['source']}")
    relay_evidence=(plan.get('market_evidence') or {}).get('theme_relay')
    if relay_evidence:
        from page_theme_relay import render_relay_followup
        try:
            render_relay_followup(relay_evidence,record['id'])
        except (ValueError,KeyError,TypeError):
            st.info('原题材预案的对照资料不完整，可继续查看和修改下方个人研究条件。')
    for item in workspace.price_checks(record, quote, today=beijing_now().strftime("%Y%m%d")):
        st.info(item["text"])
    a, b = st.columns(2)
    if a.button("查看技术图 →", key=store.key()+record["id"]+"_chart", width="stretch"):
        journey.go(journey.QUOTE_PAGE, plan["symbol"])
    if plan["mode"] != "实盘记录" and b.button("建立实盘记录", key=store.key()+record["id"]+"_real", width="stretch"):
        saved, err = store.new_real_record(record)
        st.session_state[store.key()+"_selected_record"] = saved["id"]
        _saved(saved, err)
    tabs = st.tabs(["研究与条件", "执行与结果", "原始依据与修订"])
    with tabs[0]:
        editor(record)
    with tabs[1]:
        execution(record)
    with tabs[2]:
        st.markdown("**首次关注时保存的依据**")
        for field, label in (("thesis", "依据"), ("trigger", "条件"), ("invalid", "失效")):
            st.write(f"{label}：{plan.get(field) or '待补充'}")
        for entry in reversed(record["outcome"].get("research_history", [])):
            with st.expander(f"{entry['at'][:19]} · {entry.get('reason') or '更新研究条件'}"):
                values = entry["values"]
                st.write("依据：" + values["thesis"])
                st.write("等待：" + values["trigger"])
                st.write("失效：" + values["invalid"])
                st.write("复核日：" + (values.get("review_on") or "未设置"))
                labels = {"observe_low":"观察区间下沿", "observe_high":"观察区间上沿", "invalid_below":"下方复核价",
                          "invalid_above":"上方复核价", "price_basis_date":"价位适用日", "shares":"持有数量",
                          "cost_price":"每股成本", "position_date":"持仓核对日"}
                for key, label in labels.items():
                    if values.get(key) is not None and values.get(key) != "":
                        st.write(f"{label}：{values[key]}")
        st.download_button("下载这只股票的研究", engine.plan_markdown(record), "stock-research.md", "text/markdown", key=record["id"]+"_export")
    if record.get("_save_error", {}).get("code") == "CONFLICT":
        with st.expander("解决同步冲突"):
            st.caption("先下载当前备份，再选择保留哪份内容。")
            if st.button("保留本地内容，另存新记录", key=record["id"]+"_fork"):
                import copy
                import uuid
                forked = copy.deepcopy(record)
                forked.update(id=str(uuid.uuid4()), created_at=beijing_now().isoformat(), revision=0, _synced=False)
                forked["plan"]["parent_id"] = record["id"]
                saved, err = store.save(forked)
                if not err:
                    # Reload the original cloud entry after the local version is safe.
                    store.load()[record["id"]].pop("_dirty", None)
                    store.load(force=True)
                _saved(saved, err)
            if st.button("读取云端版本，放弃本窗口未保存修改", key=record["id"]+"_reload"):
                remote, err = journal_store.load_records()
                match = next((r for r in remote or [] if r["id"] == record["id"]), None)
                if not err and match:
                    match.update(_synced=True, _dirty=False)
                    store.load()[record["id"]] = match
                    st.rerun()
                st.error("暂时无法读取对应记录，本地修改仍保留。")


def position_table(records, quotes):
    result = workspace.holdings(list(records.values()), quotes)
    if result["duplicates"]:
        st.warning("以下股票有多条未结束的实盘持仓记录，暂不汇总，请先核对：" + "、".join(result["duplicates"]))
    if not result["rows"]:
        st.info("在股票研究档案中建立实盘记录，再填写持有数量与成本，即可查看持仓分布。")
        return
    st.caption("占比仅针对本页已记录持仓；现金和未录入资产未纳入。估算浮盈亏未扣费用，分红送转后需自行更新成本与数量。")
    st.dataframe(pd.DataFrame([{"代码": r["symbol"], "名称": r["name"], "行业": r["industry"],
        "数量": r["shares"], "每股成本": r["cost"], "收盘价": r["close"], "报价日期": r["date"],
        "核对持仓日": r["position_date"], "记录市值(元)": r["value"], "估算浮盈亏(元)": r["pnl"],
        "占已记录持仓(%)": r["weight"]*100 if r["weight"] is not None else None} for r in result["rows"]]),
        hide_index=True, width="stretch")
    if result["total"] is not None:
        st.metric("已记录持仓市值", f"{result['total']:,.2f} 元")
        sectors = {}
        for row in result["rows"]:
            sectors[row["industry"]] = sectors.get(row["industry"], 0) + row["value"]
        st.dataframe(pd.DataFrame([{"行业": k, "记录市值(元)": v, "占比(%)": 100*v/result["total"]} for k,v in sectors.items()]), hide_index=True, width="stretch")
        st.caption("行业相同可以帮助发现集中持仓；ETF 成分重叠尚未接入，行业不同也不能证明风险独立。")
    else:
        st.info("部分持仓缺少同日价格或存在重复记录，暂不汇总市值与集中度。")

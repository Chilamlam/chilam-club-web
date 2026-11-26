"""Objective -> contract comparison -> personal plan -> dated review."""
from __future__ import annotations

import datetime as dt
import hashlib
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import decision_workbench as journal
import investor_journey as journey
import research_store as store
import research_workspace as workspace
import us_options as model
from us_options_data import NY, fetch_chain
from update_schedule import beijing_now


@st.cache_data(ttl=300, max_entries=60, show_spinner=False)
def _fetch(symbol, start, end):
    try:
        return fetch_chain(symbol, start, end), ""
    except Exception:
        return None, "公开期权链暂时无法读取；可以稍后刷新，或录入券商报价继续研究。"


def _money(value):
    return "上行不封顶" if value is None else f"${value:,.2f}"


def _quote_note(snapshot):
    st.write(f"**{snapshot['symbol']} 参考价 {_money(snapshot['spot'])}** · 标的日期 {snapshot['underlying_asof']}")
    stamp = dt.datetime.fromisoformat(snapshot["retrieved_at"]).astimezone(NY)
    if snapshot.get("quote_mode") == "manual":
        st.caption(f"手动录入 · 报价时间 {snapshot.get('quote_time')} · 来源：{snapshot['source']}")
    else:
        st.caption(f"Nasdaq 公开参考报价 · 本次读取 {stamp:%Y-%m-%d %H:%M %Z} · 缓存最多 5 分钟。")
        st.info("来源没有提供期权买卖价的时间戳，无法确认各腿同步或实时。下列金额用于比较预案，交易前需在券商核对组合限价。")
    age = (dt.datetime.now(NY).date() - dt.date.fromisoformat(snapshot["underlying_asof"])).days
    if age > 3:
        st.warning(f"标的参考价距纽约今日已有 {age} 个自然日，当前比较使用旧资料。")


def _manual(symbol):
    today = dt.datetime.now(NY).date()
    scope = "options_manual_" + symbol
    with st.form(scope):
        a, b, c = st.columns(3)
        spot = a.number_input("同一时点的 ETF 价格（USD）", min_value=0.01, value=None)
        expiry = b.date_input("合约到期日（纽约日期）", value=today+dt.timedelta(days=30), min_value=today+dt.timedelta(days=1))
        source = c.text_input("券商或报价来源")
        qdate = a.date_input("报价日期（纽约）", value=today, max_value=today)
        qtime = b.time_input("报价时间（纽约）", value=dt.datetime.now(NY).time().replace(second=0, microsecond=0))
        st.caption("每行填同一行权价的 Call / Put；只用一侧时另一侧留空。价格按每股美元填写，买价 Bid、卖价 Ask 不要颠倒。")
        frame = pd.DataFrame({"行权价": [None, None, None], "Call Bid": [None]*3, "Call Ask": [None]*3,
                              "Put Bid": [None]*3, "Put Ask": [None]*3}, dtype=float)
        edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch", key=scope+"_grid",
                                column_config={k: st.column_config.NumberColumn(k, min_value=0.0, format="%.3f") for k in frame})
        standard = st.checkbox("已核对这些是每张对应 100 股的标准合约，非调整合约")
        submitted = st.form_submit_button("使用这批报价比较")
    if submitted:
        stamp = dt.datetime.combine(qdate, qtime, NY)
        options, seen = [], set()
        try:
            model.positive(spot, "ETF 价格")
            if not source.strip() or not standard:
                raise ValueError("请填写来源并核对标准合约。")
            if stamp > dt.datetime.now(NY):
                raise ValueError("报价时间不能在未来。")
            for row in edited.to_dict("records"):
                strike = model.numeric(row["行权价"])
                if not strike:
                    continue
                for kind, label in (("C", "Call"), ("P", "Put")):
                    ident = model.contract_id(symbol, expiry.isoformat(), kind, strike)
                    if ident in seen:
                        raise ValueError("行权价重复，请合并到一行。")
                    seen.add(ident)
                    options.append(dict(id=ident, expiry=expiry.isoformat(), kind=kind, strike=strike, multiplier=100,
                                        bid=model.numeric(row[label+" Bid"]), ask=model.numeric(row[label+" Ask"]),
                                        volume=None, open_interest=None))
            if not any(model.valid_quote(o) for o in options):
                raise ValueError("至少需要一张合约的有效双边报价（0 < Bid ≤ Ask）。")
            st.session_state[scope+"_snapshot"] = dict(symbol=symbol, spot=spot, options=options, source=source.strip(),
                underlying_asof=qdate.isoformat(), retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                quote_time=stamp.isoformat(), quote_mode="manual")
        except ValueError as exc:
            st.error(str(exc))
    return st.session_state.get(scope+"_snapshot")


def _read_snapshot(symbol):
    mode = st.radio("报价来源", ["公开期权链", "录入券商报价"], horizontal=True, key="us_options_source")
    if mode == "录入券商报价":
        return _manual(symbol)
    now = dt.datetime.now(NY)
    months = []
    for offset in range(4):
        year, month = divmod(now.year*12 + now.month-1 + offset, 12)
        months.append(dt.date(year, month+1, 1))
    a, b = st.columns([3, 1])
    month = a.selectbox("到期月份", months, index=1, format_func=lambda d: f"{d:%Y年%m月}", key="us_options_month")
    end = (month + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    start = max(month, now.date()+dt.timedelta(days=1))
    if b.button("刷新期权链", key="us_options_refresh", width="stretch"):
        _fetch.clear(symbol, start.isoformat(), end.isoformat())
    key = f"us_options_chain_{symbol}_{month}"
    with st.spinner("读取期权链…"):
        snapshot, error = _fetch(symbol, start.isoformat(), end.isoformat())
    if snapshot:
        st.session_state[key] = snapshot
    elif error:
        st.warning(error)
        if key in st.session_state:
            st.caption("下方保留本次会话上一次成功读取的报价，读取时间保持原值。")
    return st.session_state.get(key)


def _strike(label, values, near, key):
    if not values:
        return None
    values = sorted(set(values))
    return st.selectbox(label, values, index=min(range(len(values)), key=lambda i: abs(values[i]-near)),
                        format_func=lambda v:f"${v:g}", key=key)


def _leg_table(plan):
    st.dataframe(pd.DataFrame([{"操作": "买入" if l["side"] == 1 else "卖出", "合约": l["id"],
        "类型": "Call" if l["kind"] == "C" else "Put", "行权价 USD": l["strike"], "张数": l["quantity"],
        "Bid": l["bid"], "Ask": l["ask"], "测算开仓价/股": l["entry"],
        "成交量": l.get("volume"), "持仓量": l.get("open_interest")} for l in plan["legs"]]), hide_index=True, width="stretch")


def _chart(plans, target, stress):
    spot = plans[0]["spot"]
    low = max(0, min(spot*.8, target*.97, stress*.97, *[l["strike"]*.97 for p in plans for l in p["legs"]]))
    high = max(spot*1.2, target*1.03, stress*1.03, *[l["strike"]*1.03 for p in plans for l in p["legs"]])
    prices = sorted({low+(high-low)*i/120 for i in range(121)} | {l["strike"] for p in plans for l in p["legs"]} | {spot, target, stress})
    fig = go.Figure()
    for p in plans:
        fig.add_trace(go.Scatter(x=prices, y=[model.expiry_pnl(p, x) for x in prices], name=model.STRATEGIES[p["strategy"]], mode="lines"))
    if plans[0]["shares"]:
        fig.add_trace(go.Scatter(x=prices, y=[plans[0]["shares"]*(x-spot) for x in prices], name="相同持仓不加保护", line=dict(dash="dot")))
    else:
        fig.add_trace(go.Scatter(x=prices, y=[0]*len(prices), name="继续等待（忽略现金利息）", line=dict(dash="dot")))
    fig.add_hline(y=0, line_color="#888")
    fig.add_vline(x=spot, line_dash="dot", annotation_text="当前参考价")
    fig.update_layout(xaxis_title="到期时 ETF 价格（USD）", yaxis_title="自当前参考价起的盈亏（USD）", height=390,
                      margin=dict(l=15,r=15,t=25,b=20), legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")
    st.caption("这是到期盈亏图，已扣输入的往返费用；到期前价格还受时间价值和隐含波动率影响。未计分红、税费、融资利息及提前指派后的额外成本。")


def _save_plan(plan, target, stress, budget):
    scope = f"{store.key()}_option_plan_{plan['symbol']}_{plan['strategy']}_{plan['expiry']}"
    today = dt.datetime.now(NY).date()
    expiry = dt.date.fromisoformat(plan["expiry"])
    stats = model.metrics(plan)
    with st.expander("把这组策略变成自己的预案", expanded=True):
        with st.form(scope):
            thesis = st.text_area("交易或保护的依据", placeholder="写明事件、来源和日期，以及为什么选择这个期限。")
            trigger = st.text_input("什么条件满足才行动", placeholder="例如：标的确认关键位置，且券商组合报价不超过我接受的成本。")
            invalid = st.text_input("什么变化会推翻判断", placeholder="写明价位、事件或时间条件；与下方极端情景区分。")
            exit_rule = st.text_input("止盈、止损或提前退出安排", placeholder="请写明计划平仓的条件和时间；不要把到期图当成到期前止损保证。")
            due = st.date_input("下次复核日（纽约日期）", value=min(today+dt.timedelta(days=1), expiry-dt.timedelta(days=1)),
                                min_value=today, max_value=expiry-dt.timedelta(days=1))
            cost = st.text_input("可接受的成本与执行限制", value="下单前核对组合净价、费用与提前指派安排；按上方损失预算复核。")
            st.caption("先保存为观察预案。合约、参考报价和情景会一起封存；在研究档案中记录实际执行和美元盈亏。")
            submitted = st.form_submit_button("保存期权预案", type="primary")
        if submitted:
            try:
                evidence = dict(plan, target_price=target, stress_price=stress, loss_budget=budget)
                fields = dict(symbol=plan["symbol"]+".US", name=plan["symbol"]+" · "+model.STRATEGIES[plan["strategy"]],
                    style="us_options", currency="USD", mode="模拟观察", asof=today.strftime("%Y%m%d"),
                    thesis=thesis, trigger=trigger, invalid=invalid, exit_rule=exit_rule, cost_risk=cost,
                    manual_evidence=f"来源：{plan['source']}；读取：{plan['retrieved_at']}；期权时间：{plan.get('quote_time') or '来源未提供'}",
                    data_current=False, market_evidence={"us_options": evidence})
                signature = hashlib.sha256(json.dumps([fields, due.isoformat()], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                records = store.load()
                previous = st.session_state.get(scope+"_saved")
                if previous and previous[0] == signature and previous[1] in records:
                    st.success("这份预案已经保存，未重复创建。")
                else:
                    record = journal.new_plan(fields, now=beijing_now().isoformat())
                    record = workspace.revise(record, dict(workspace.research(record), review_on=due.isoformat()), now=beijing_now().isoformat(), reason="首次期权预案")
                    saved, err = store.save(record)
                    st.session_state[scope+"_saved"] = (signature, saved["id"])
                    (st.warning if err else st.success)(store.save_message(saved, err))
            except ValueError as exc:
                st.error(str(exc))
        if st.button("打开研究档案 →", key=scope+"_open"):
            previous = st.session_state.get(scope+"_saved")
            if previous:
                st.session_state[store.key()+"_selected_record"] = previous[1]
            journey.go(journey.WORKBENCH_PAGE, plan["symbol"]+".US")


def render_us_options():
    st.title("美股期权策略")
    st.write("先明确要表达的方向或保护目标，再比较具体合约的成本、得失和退出安排。")
    a, b = st.columns([1, 2])
    symbol = a.selectbox("标的 ETF", list(model.UNIVERSE), format_func=lambda s:f"{s} · {model.UNIVERSE[s]}", key="us_options_symbol")
    goal = b.radio("这次要解决什么", ["看涨", "看跌", "保护已有持仓"], horizontal=True, key="us_options_goal")
    snapshot = _read_snapshot(symbol)
    if not snapshot:
        return
    _quote_note(snapshot)
    today = dt.datetime.now(NY).date()
    expiries = sorted({o["expiry"] for o in snapshot["options"] if dt.date.fromisoformat(o["expiry"]) > today})
    if not expiries:
        st.info("这批报价没有纽约今日之后的到期合约，请换月份或刷新。")
        return
    expiry = st.selectbox("到期日（纽约日期）", expiries,
        index=min(range(len(expiries)), key=lambda i:abs((dt.date.fromisoformat(expiries[i])-today).days-30)),
        format_func=lambda s:f"{s} · 距到期 {(dt.date.fromisoformat(s)-today).days} 个自然日", key=f"options_exp_{symbol}_{expiries[0][:7]}")
    scope = f"options_{symbol}_{goal}_{expiry}"
    spot = snapshot["spot"]
    hedge = goal == "保护已有持仓"
    a, b, c = st.columns(3)
    target_pct = a.number_input("到期目标涨跌幅（%）", min_value=-99.0, max_value=200.0, value=-10.0 if hedge else 5.0 if goal == "看涨" else -5.0, step=1.0, key=scope+"_target")
    stress_pct = b.number_input("额外压力情景涨跌幅（%）", min_value=-100.0, max_value=300.0, value=-20.0 if hedge else -10.0 if goal == "看涨" else 10.0, step=1.0, key=scope+"_stress")
    budget = c.number_input("最大可接受损失（USD）", min_value=0.0, value=5000.0 if hedge else 1000.0, step=100.0, key=scope+"_budget")
    a, b, c = st.columns(3)
    shares = int(a.number_input("拟保护的同一 ETF 已有股数", min_value=0, value=100, step=100, key=scope+"_shares")) if hedge else 0
    count = int(b.number_input("组合组数（每腿相同张数）", min_value=1, max_value=10000, value=1, step=1, key=scope+"_count"))
    fee = c.number_input("每张期权往返费用预算（USD）", min_value=0.0, value=1.30, step=0.10, key=scope+"_fee", help="默认仅为可修改的估算，不代表任何券商费率；包括开仓与平仓。")
    target, stress = spot*(1+target_pct/100), spot*(1+stress_pct/100)
    if hedge:
        st.caption("保护只对应所选 ETF 的已有持仓。组合损益从当前参考价开始计算；账户历史成本、其他股票及人民币汇率风险未纳入。")
    options = [o for o in snapshot["options"] if o["expiry"] == expiry and model.valid_quote(o)]
    kind = "C" if goal == "看涨" else "P"
    buys = [o["strike"] for o in options if o["kind"] == kind and (not hedge or o["strike"] <= spot)]
    a, b = st.columns(2)
    with a:
        buy = _strike("买入腿行权价（Call）" if kind == "C" else "买入腿行权价（Put）", buys, spot*.95 if hedge else spot, scope+"_buy")
    if buy is None:
        st.warning("所选期限缺少有效双边报价，无法比较；请换到期日或录入券商报价。")
        return
    sells = [o["strike"] for o in options if o["kind"] == ("C" if hedge else kind) and
             (o["strike"] > (spot if hedge else buy) if goal != "看跌" else o["strike"] < buy)]
    with b:
        sell = _strike("卖出腿行权价（领口 / 价差）", sells, spot*1.05 if hedge else target, scope+f"_sell_{buy}")
    st.caption("初始行权价取接近平值、方向目标或保护位置的可用合约；可以自由改选。缺失、零 Bid 或倒挂报价已排除。")
    strategies = ("protective_put", "collar") if hedge else ("long_call", "bull_call") if goal == "看涨" else ("long_put", "bear_put")
    plans, errors = [], []
    for name in strategies:
        try:
            plans.append(model.make_strategy(snapshot, name, expiry, buy, sell, contracts=count, shares=shares, fee=fee))
        except ValueError as exc:
            errors.append(f"{model.STRATEGIES[name]}：{exc}")
    for error in errors:
        st.warning(error)
    if not plans:
        return
    st.subheader("按你的目标，哪种取舍能接受")
    for column, p in zip(st.columns(len(plans)), plans):
        m = model.metrics(p)
        with column, st.container(border=True):
            st.markdown(f"**{model.STRATEGIES[p['strategy']]}**")
            st.caption("超过你的损失预算" if m["max_loss"] > budget else "在你的损失预算内")
            a, b = st.columns(2)
            a.metric("净支出（含费用）", _money(p["premium"]+p["fees"]))
            b.metric("到期最大损失", _money(m["max_loss"]))
            a.metric("目标情景盈亏", _money(model.expiry_pnl(p,target)))
            b.metric("到期盈亏平衡价", " / ".join(f"${v:.2f}" for v in m["breakevens"]) or "无")
            st.write("到期最大收益："+_money(m["max_profit"]))
            st.caption(f"净权利金 {_money(p['premium'])}，往返费用预算 {_money(p['fees'])}。")
    st.caption("金额均为 USD，负的净支出表示净收取。最大损失为到期结构测算，券商保证金、临时交割资金与提前指派须另行核对。")
    scenarios = []
    for label, price in ((f"目标 {target_pct:+g}%", target), ("价格不变", spot), (f"压力 {stress_pct:+g}%", stress)):
        row = {"到期场景": label, "ETF价格 USD": round(price, 2)}
        row.update({model.STRATEGIES[p["strategy"]]+"盈亏 USD": round(model.expiry_pnl(p,price),2) for p in plans})
        if hedge:
            row["相同持仓不加保护 USD"] = round(shares*(price-spot),2)
        scenarios.append(row)
    st.dataframe(pd.DataFrame(scenarios), hide_index=True, width="stretch")
    within = [p for p in plans if model.metrics(p)["max_loss"] <= budget]
    if not within:
        st.warning("当前两种结构都超过你设定的损失预算，先调整合约或数量，也可以继续等待。")
    elif not hedge and all(model.expiry_pnl(p, target) <= 0 for p in plans):
        st.info("即使达到你填写的方向目标，这些合约到期仍不盈利。需要重新核对预期幅度、权利金与期限。")
    if len(plans) == 2:
        saved = plans[0]["premium"]-plans[1]["premium"]+plans[0]["fees"]-plans[1]["fees"]
        limit = f"对应的 {100*count} 股收益在 **${sell:g}** 以上不再增加" if goal != "看跌" else f"标的跌到 **${sell:g}** 以下，价差收益不再增加"
        st.write(f"加入卖出腿后，含费用的净支出减少 **{_money(saved)}**；{limit}。")
    if hedge:
        residual = model.metrics(plans[0])["residual_shares"]
        st.write(f"保护覆盖 **{count*100}/{shares} 股**；仍有 **{residual} 股**随价格下跌而承受损失。无保护时，压力情景的持仓盈亏为 **{_money(shares*(stress-spot))}**。")
    _chart(plans, target, stress)
    chosen = st.radio("继续研究哪一组", list(range(len(plans))), format_func=lambda i:model.STRATEGIES[plans[i]["strategy"]], horizontal=True, key=scope+"_chosen")
    plan = plans[chosen]
    _leg_table(plan)
    with st.expander("核对交割数量与资金需求"):
        st.dataframe(pd.DataFrame([{"合约": l["id"], "可能发生的交割":
            ("买入" if (l["kind"] == "C") == (l["side"] == 1) else "卖出") + f" {l['quantity']*100} 股 {symbol}",
            "该腿行权价对应金额 USD": l["strike"]*l["quantity"]*100} for l in plan["legs"]]), hide_index=True, width="stretch")
        st.caption("逐腿列示名义交割，不把两腿抵消视为保证。需要买入股票时可能临时占用相应资金；卖出时须有股票或处理券商形成的空头。以券商审批、保证金与行权安排为准。")
    st.write("**执行前的关键安排：** SPY、QQQ、IWM 是美式、实物交割的 ETF 期权，每张标准合约对应 100 股。买方行权也可能需要交割资金；价差或领口的卖出腿可能提前被指派，尤其需核对除息日、券商截止时间和临近到期的平仓安排。")
    _save_plan(plan, target, stress, budget)
    with st.expander("合约规则与策略来源"):
        st.write("买入按 Ask、卖出按 Bid 测算。两腿报价不能保证同时成交；下单时应复核组合净价。到期情景不提供胜率预测。")
        for label, url in model.SOURCES.items():
            st.markdown(f"[{label}]({url})")


def render_saved_options(record):
    """Frozen entry and optional same-leg mark, inside the existing private journal."""
    plan = (record["plan"].get("market_evidence") or {}).get("us_options")
    if not plan:
        st.info("这份旧记录未保存完整期权腿，请回到期权策略页重新建立。")
        return
    try:
        model.validate_evidence(plan, record["plan"]["symbol"])
    except (ValueError, TypeError, KeyError):
        st.warning("原合约资料不完整，无法重算；原始研究文字仍可查看。")
        return
    st.markdown(f"**{model.STRATEGIES[plan['strategy']]} · 到期 {plan['expiry']} · {plan['contracts']} 组 · USD**")
    st.caption(f"原参考价 ${plan['spot']:g} · 原报价来源 {plan['source']} · 原读取时间 {plan['retrieved_at']} · 原期权时间 {plan.get('quote_time') or '来源未提供'}")
    _leg_table(plan)
    stats = model.metrics(plan)
    st.write(f"原结构到期最大损失 **{_money(stats['max_loss'])}**；原净权利金支出 **{_money(plan['premium'])}**；往返费用预算 **{_money(plan['fees'])}**。")
    target = plan.get("target_price", plan["spot"])
    stress = plan.get("stress_price", plan["spot"]*.9)
    with st.expander("原始目标与压力情景"):
        st.write(f"目标价 ${target:.2f} → 到期盈亏 {_money(model.expiry_pnl(plan,target))}；压力价 ${stress:.2f} → 到期盈亏 {_money(model.expiry_pnl(plan,stress))}。")
        _chart([plan], target, stress)
    today = dt.datetime.now(NY).date()
    expiry = dt.date.fromisoformat(plan["expiry"])
    due = workspace.research(record).get("review_on")
    if due and due <= today.strftime("%Y%m%d"):
        st.info(f"约定复核日 {due} 已到：检查原触发、失效和退出条件，再记录实际行动。")
    if expiry <= today:
        st.warning("已到到期日或已过期，请核对券商的平仓、行权和指派记录。本页不会用 ETF 收盘价自动认定真实盈亏。")
    elif st.button("用原合约复核参考平仓金额", key=store.key()+record["id"]+"_option_mark"):
        _fetch.clear(plan["symbol"], plan["expiry"], plan["expiry"])
        with st.spinner("查询相同到期日和行权价…"):
            snapshot, error = _fetch(plan["symbol"], plan["expiry"], plan["expiry"])
        if error:
            st.warning(error)
        elif snapshot:
            _quote_note(snapshot)
            pnl = model.liquidation_pnl(plan, snapshot)
            if pnl is None:
                st.info("至少一条原合约缺少双边报价，暂不估算平仓盈亏。")
            else:
                st.metric("按参考 Bid / Ask 平仓的估算盈亏（USD）", _money(pnl))
                st.caption("从原参考入场价计算：卖出长腿取 Bid，买回短腿取 Ask，并扣原往返费用预算。它不是券商持仓盈亏，不自动写入执行结果。")
    if st.button("回到期权策略比较 →", key=store.key()+record["id"]+"_options_back"):
        st.session_state["us_options_symbol"] = plan["symbol"]
        journey.go(model.PAGE)

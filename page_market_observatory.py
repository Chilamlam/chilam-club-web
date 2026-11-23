"""Market observations with visible dates, bounded lists and optional detail charts."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import global_markets as global_data
import market_watch as calc
import market_watch_data as data
from investor_journey import go as navigate, QUOTE_PAGE, WORKBENCH_PAGE
from site_data import load_csv
from stock_search import matches_stock, stock_label
from update_schedule import beijing_now

LOCAL_PAGE = "📈 资金与基差"
GLOBAL_PAGE = "🌍 海外预期与链上市场"


def fmt(value, digits=2, suffix=""):
    value = calc.numeric(value)
    return f"{value:,.{digits}f}{suffix}" if value is not None else "—"


def display_time(value):
    try:
        return pd.Timestamp(value).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(value or "暂无")


@st.cache_data(ttl=300, show_spinner=False)
def fetch_snapshot(kind):
    collectors={"futures":data.collect_futures, "etf":data.collect_etf,
                "polymarket":global_data.collect_polymarket, "onchain":global_data.collect_onchain}
    try:
        return collectors[kind]()
    except Exception as exc:
        return data.bundle([], "", [f"本次读取失败（{type(exc).__name__}）"])


@st.cache_data(ttl=300, show_spinner=False)
def fetch_history(kind, code, days=30):
    try:
        return (global_data.probability_history(code, days) if kind == "polymarket" else global_data.chain_history(code)), None
    except Exception as exc:
        return None, f"本次历史数据未取得（{type(exc).__name__}），可稍后刷新重试。"


def snapshot(kind, *, live=False):
    archive = data.read_archive(kind)
    refresh = st.button("刷新数据", key="observe_refresh_"+kind)
    if refresh:
        fetch_snapshot.clear(kind)
        fetch_history.clear()
    if live or refresh or not archive.get("rows"):
        with st.spinner("读取公开市场数据…"):
            fresh = fetch_snapshot(kind)
        if fresh.get("rows"):
            st.session_state["observe_snapshot_"+kind] = fresh
        else:
            st.warning("本次更新未取得有效数据。下面如有内容，使用明确标注时间的上次快照。")
            for error in fresh.get("errors", []):
                st.caption(error)
    result = st.session_state.get("observe_snapshot_"+kind) or archive
    if archive.get("collected_at", "") > result.get("collected_at", ""):
        result = archive
    if not result.get("rows"):
        st.info("当前暂无可展示的归档。可点击刷新；来源恢复后会继续更新。")
        return None
    st.caption(f"采集时间 {display_time(result.get('collected_at'))}（北京时间） · {result.get('source', '')}")
    try:
        age = (beijing_now()-dt.datetime.fromisoformat(result["collected_at"])).total_seconds()
        if live and age > 900:
            st.warning("当前为历史快照，已超过 15 分钟；请结合采集时间查看。")
    except (ValueError, TypeError, KeyError):
        st.warning("快照缺少有效采集时间。")
    if result.get("errors"):
        with st.expander("本次未取得的数据"):
            for error in result["errors"]:
                st.write(error)
    if result.get("last_attempt"):
        st.caption("最近一次定时更新未成功，仍保留上次有效归档。")
    return result


def table(frame, key, *, numeric=(), download=True):
    if frame.empty:
        st.info("当前筛选没有记录。")
        return
    pages=(len(frame)+19)//20
    # Date/filter changes can shrink the number of pages.
    if st.session_state.get(key+"_page",1) > pages:
        st.session_state[key+"_page"] = 1
    if pages>1:
        page = st.selectbox("页码",range(1,pages+1),key=key+"_page", format_func=lambda n:f"第 {n} / {pages} 页")
    else:
        page=1
    st.caption(f"共 {len(frame)} 条 · 当前 {(page-1)*20+1}—{min(page*20,len(frame))} 条")
    st.dataframe(frame.iloc[(page-1)*20:page*20], hide_index=True,width="stretch",
        column_config={col:st.column_config.NumberColumn(format="%.2f") for col in numeric})
    if download:
        st.download_button("下载当前筛选的全部记录",frame.to_csv(index=False).encode("utf-8-sig"),
                           file_name=key+".csv",mime="text/csv",key=key+"_download")


def chart(frame, fields, *, title, ylabel, key, bars=()):
    if frame.empty:
        return
    fig=go.Figure()
    x=pd.to_datetime(frame["date"],format="%Y%m%d")
    for col, label in fields.items():
        if col not in frame:
            continue
        if col in bars:
            fig.add_bar(x=x,y=frame[col],name=label)
        else:
            fig.add_scatter(x=x,y=frame[col],name=label,mode="lines+markers",connectgaps=False)
    fig.update_layout(title=title,height=300,margin=dict(l=15,r=15,t=45,b=10),yaxis_title=ylabel,
                      legend_orientation="h",hovermode="x unified")
    st.plotly_chart(fig,width="stretch",key=key)


def render_breadth():
    st.subheader("A 股涨跌与融资余额")
    st.write("对照市场上涨覆盖面与融资余额的变化，回看上涨是否只集中在少数股票。")
    cal=data.calendar()
    breadth=calc.breadth_history(load_csv("data/sentiment/breadth_history.csv"),load_csv("data/market_sentiment.csv"))
    margin=calc.financing_history(load_csv("data/funding/margin_history.csv"),cal)
    if breadth.empty:
        st.info("暂无市场宽度归档。")
        return
    last=breadth.iloc[-1]
    expected=data.last_close_day()
    if expected and last['date'] < expected:
        st.warning(f"涨跌归档截至 {last['date']}，最近已收盘交易日为 {expected}。")
    a,b,c,d=st.columns(4)
    a.metric("上涨 / 下跌",f"{fmt(last['up'],0)} / {fmt(last['down'],0)}")
    b.metric("中位涨跌幅",fmt(last.get("median_pct"),2,"%"))
    c.metric("成交额",fmt(last.get("total_amount_yi"),0," 亿"))
    if not margin.empty:
        row=margin.iloc[-1]
        d.metric("沪深融资余额",fmt(row["balance_yi"],2," 亿"),delta=fmt(row["change_yi"],2," 亿"),delta_color="off")
        st.caption(f"涨跌统计日 {last['date']} · 融资披露对应交易日 {row['date']}；融资通常晚一个交易日。")
    else:
        d.metric("沪深融资余额","—")
    months=sorted({d[:6] for d in breadth["date"]},reverse=True)
    month=st.selectbox("查看月份",["全部"]+months,key="breadth_month")
    if month!="全部":
        breadth=breadth[breadth["date"].str.startswith(month)]
        if not margin.empty:
            margin=margin[margin["date"].str.startswith(month)]
    chart(breadth,{"up":"上涨家数","down":"下跌家数"},title="上涨与下跌覆盖面",ylabel="家",key="obs_breadth",bars=("up","down"))
    if not margin.empty:
        # Keep missing disclosure sessions as gaps on the line.
        days=[d for d in cal.get("open_dates",[]) if margin["date"].min() <= d <= margin["date"].max()]
        plotted=margin.set_index("date").reindex(days).rename_axis("date").reset_index()
        chart(plotted,{"balance_yi":"沪深融资余额"},title="融资余额（独立日期口径）",ylabel="亿元",key="obs_margin")
    joined=breadth.merge(margin[["date","balance_yi","change_yi"]],on="date",how="outer") if not margin.empty else breadth
    joined=joined.sort_values("date",ascending=False).rename(columns={"date":"交易日","up":"上涨","down":"下跌",
        "median_pct":"中位涨跌(%)","mean_pct":"平均涨跌(%)","up_ratio":"上涨占比(%)", "total_amount_yi":"成交额(亿)",
        "balance_yi":"沪深融资余额(亿)","change_yi":"较前一交易日(亿)"})
    table(joined,"breadth_margin",numeric=[c for c in joined if c not in ("交易日","上涨","下跌")])
    with st.expander("统计范围与来源"):
        st.write("涨跌和中位数来自 Tushare 当日有行情的 A 股样本（含北交所）；平盘不计入上涨或下跌，上涨占比＝上涨÷（上涨＋下跌）。融资余额使用沪深两所合计，不含北交所，因此与参考站的沪深京合计可能不同。")
        st.write("融资日变化只在前一交易日也有披露时计算；缺日期留空。余额变化同时受融资买入与偿还等影响，不等于当日资金净流入。")
        st.link_button("融资数据字段说明", "https://tushare.pro/document/2?doc_id=58")


def render_futures():
    st.subheader("股指期货贴水")
    st.write("对照同日现货与各到期月份的期货收盘价，观察不同指数的期限结构。")
    snap=snapshot("futures")
    if not snap:
        return
    frame=pd.DataFrame(snap["rows"])
    date=st.selectbox("收盘日期",sorted(frame["date"].unique(),reverse=True),key="basis_date")
    prefix=st.radio("股指",["全部"]+list(calc.INDEXES),format_func=lambda p:p if p=="全部" else f"{p} · {calc.INDEXES[p]['name']}",horizontal=True,key="basis_index")
    subset=frame[frame["date"]==date].copy()
    if prefix!="全部":
        subset=subset[subset["prefix"]==prefix]
    st.caption(f"同日收盘比较 {date} · 正数为贴水（现货高于期货），负数为升水；未扣分红。")
    expected=data.last_close_day() or snap.get("cutoff",date)
    if frame['date'].max() < expected:
        st.warning(f"最新可核对收盘日为 {frame['date'].max()}；最近已收盘交易日 {expected} 的同日数据尚未齐全。")
    shown=subset.sort_values(["prefix","expiry"])[["index","contract","spot","future","discount","discount_pct","annual_pct","days","expiry","open_interest"]].rename(columns={
        "index":"指数","contract":"合约","spot":"现货收盘","future":"期货收盘","discount":"贴水(点)","discount_pct":"贴水率(%)",
        "annual_pct":"贴水年化(%)","days":"余自然日","expiry":"预计到期日","open_interest":"持仓量(手)"})
    table(shown,"basis_contracts",numeric=["现货收盘","期货收盘","贴水(点)","贴水率(%)","贴水年化(%)"])
    selected=st.selectbox("查看期限结构与历史",list(calc.INDEXES),format_func=lambda p:calc.INDEXES[p]["name"],key="basis_detail")
    today=subset[subset["prefix"]==selected] if prefix in ("全部",selected) else frame[(frame["date"]==date)&(frame["prefix"]==selected)]
    today=today.sort_values("days")
    curve=go.Figure(go.Scatter(x=today["days"],y=today["discount_pct"],text=today["contract"],mode="lines+markers+text",textposition="top center"))
    curve.update_layout(height=270,margin=dict(l=15,r=15,t=20,b=10),xaxis_title="剩余自然日",yaxis_title="贴水率 (%)")
    st.plotly_chart(curve,width="stretch",key="basis_curve")
    history=frame[(frame["prefix"]==selected)&(frame["date"]<=date)]
    contracts=sorted(today["contract"].tolist())
    fig=go.Figure()
    for contract in contracts:
        h=history[history["contract"]==contract].sort_values("date")
        fig.add_scatter(x=pd.to_datetime(h["date"]),y=h["annual_pct"],name=contract,mode="lines",connectgaps=False)
    fig.update_layout(height=300,yaxis_title="未扣分红贴水年化 (%)",legend_orientation="h",margin=dict(l=15,r=15,t=20,b=10))
    st.plotly_chart(fig,width="stretch",key="basis_history")
    with st.expander("按自己的分红假设计算净贴水"):
        if contracts:
            contract=st.selectbox("计算合约",contracts,key="basis_div_contract")
            points=st.number_input("到期前预计分红点数",min_value=0.0,value=None,step=1.0,key="basis_div_points",
                help="填入你核对过的合约剩余期限分红预估；留空不计算。")
            row=today[today["contract"]==contract].iloc[0].to_dict()
            net,annual=calc.net_basis(row,points)
            a,b=st.columns(2)
            a.metric("按所填假设的净贴水",fmt(net,2," 点")); b.metric("净贴水年化",fmt(annual,2,"%"))
    st.caption("贴水年化＝(现货−期货)÷现货×365÷余自然日；到期日不年化。到期日按第三个周五和现有交易日历推算，超出日历的节假日待核对。年化值不是可锁定收益，也不能单独判断涨跌。")
    st.link_button("中金所合约与日行情", "http://www.cffex.com.cn/rtj/")


def render_etf():
    st.subheader("宽基 ETF 成交与放量")
    st.write("用同一批宽基 ETF 的成交额观察交易活跃度，再查看具体指数与基金走势。")
    snap=snapshot("etf")
    if not snap:
        return
    frame=calc.etf_activity(pd.DataFrame(snap["rows"]),data.calendar())
    if frame.empty:
        st.info("日线存在冲突或缺少有效日期，暂不展示。")
        return
    date=st.selectbox("ETF 收盘日期",sorted(frame["date"].unique(),reverse=True),key="etf_activity_date")
    selected_index=st.multiselect("跟踪指数（留空为完整观察池）",sorted(set(calc.ETF_POOL.values())),key="etf_activity_indices")
    symbols=[c for c,index in calc.ETF_POOL.items() if not selected_index or index in selected_index]
    pool=calc.etf_pool_history(frame,symbols)
    pooled=pool[pool["date"]==date]
    if not pooled.empty:
        row=pooled.iloc[0]
        a,b,c=st.columns(3)
        a.metric("观察池成交额",fmt(row["amount_yi"],2," 亿")); b.metric("此前20日均额",fmt(row["mean20_yi"],2," 亿")); c.metric("较此前20日",fmt(row["amount_ratio"],2," 倍"))
        st.caption(f"收盘日 {date} · 当日覆盖 {int(row['coverage'])}/{int(row['expected'])} 只；缺少成员时不汇总池子成交额。")
    else:
        st.info("所选指数在该日期没有日线；可切换指数或日期。")
    expected=data.last_close_day() or snap.get("cutoff",date)
    if frame['date'].max() < expected:
        st.warning(f"最新取得日线 {frame['date'].max()}，最近已收盘交易日 {expected} 尚未取得。")
    chart(pool[pool["date"]<=date].tail(60),{"amount_yi":"成交额","mean20_yi":"此前20日均额"},
          title="固定观察池成交额",ylabel="亿元",key="etf_activity_pool",bars=("amount_yi",))
    a,b,c=st.columns([2,1,1])
    query=a.text_input("查找 ETF",placeholder="名称、代码、拼音首字母或指数",key="etf_activity_query")
    threshold=b.number_input("成交额倍数下限",min_value=0.0,value=None,step=.1,key="etf_activity_threshold")
    order=c.selectbox("排列",["成交额","放量倍数","代码"],key="etf_activity_order")
    today=frame[(frame["date"]==date)&frame["symbol"].isin(symbols)].copy()
    mask=pd.Series([matches_stock(query,r['name'],r['symbol'],r['index']) for r in today.to_dict('records')],index=today.index,dtype=bool)
    today=today.loc[mask]
    if threshold is not None:
        today=today[today["amount_ratio"]>=threshold]
    order_col={"成交额":"amount_yi","放量倍数":"amount_ratio","代码":"symbol"}[order]
    today=today.sort_values(order_col,ascending=order=="代码",na_position="last")
    columns={"symbol":"代码","name":"名称","index":"跟踪指数","close":"收盘价","change_pct":"收盘较前日(%)",
        "amount_yi":"成交额(亿)","mean20_yi":"此前20日均额(亿)","amount_ratio":"放量倍数","baseline_days":"基准天数","source":"日线来源"}
    table(today.reindex(columns=list(columns)).rename(columns=columns),"etf_activity",numeric=["收盘价","收盘较前日(%)","成交额(亿)","此前20日均额(亿)","放量倍数"])
    if not today.empty:
        options=today.set_index("symbol")["name"].to_dict()
        symbol=st.selectbox("查看 ETF 成交历史",list(options),format_func=lambda c:stock_label(options[c],c),key="etf_activity_stock")
        chart(frame[(frame["symbol"]==symbol)&(frame["date"]<=date)].tail(60),{"amount_yi":"成交额","mean20_yi":"此前20日均额"},
            title=options[symbol],ylabel="亿元",key="etf_activity_single",bars=("amount_yi",))
        a,b=st.columns(2)
        if a.button("查看这只 ETF 的技术图 →",key="etf_activity_chart",width="stretch"):
            navigate(QUOTE_PAGE,symbol)
        if b.button("为这只 ETF 建立研究档案 →",key="etf_activity_track",width="stretch"):
            navigate(WORKBENCH_PAGE,symbol)
    with st.expander("历史逐日统计与口径"):
        table(pool.sort_values("date",ascending=False).rename(columns={"date":"交易日","amount_yi":"成交额(亿)",
            "mean20_yi":"此前20日均额(亿)","amount_ratio":"放量倍数","coverage":"取得只数","expected":"观察池只数"}),
            "etf_pool_history",numeric=["成交额(亿)","此前20日均额(亿)","放量倍数"])
        st.write("固定收录20只宽基ETF。成交额比较使用此前20个完整交易日，不含当日；缺一个基准日就不显示倍数。来源切换时整段日线替换。价格未复权，收盘较前日的变化会受到分红除息影响。放量表示交易增加，不能确认买方身份，也不等于净申购。当前为收盘跟踪，未将盘中半天成交额与全天均额比较。")


def render_polymarket():
    st.subheader("Polymarket 宏观与金融预期")
    st.write("查看不同事件结果的市场隐含概率、24小时变化和成交规模，点击事件核对结算条件。")
    snap=snapshot("polymarket",live=True)
    if not snap:
        return
    events=snap["rows"]
    a,b=st.columns([1,2])
    cat=a.selectbox("事件类别",["全部"]+sorted({e['category'] for e in events}),key="poly_category")
    query=b.text_input("搜索事件",placeholder="中文摘要或英文关键词，如 Fed、CPI、tariff",key="poly_query")
    events=[e for e in events if (cat=="全部" or e['category']==cat) and (not query or query.casefold() in (e['title']+e['original']).casefold())]
    if not events:
        st.info("当前条件下没有事件，可清空关键词或切换分类。")
        return
    rows=[]
    for e in events:
        leader=max(e['markets'],key=lambda m:m['probability'])
        rows.append({"事件":e['title'],"类别":e['category'],"当前价格最高的结果":leader['label'],"隐含概率(%)":leader['probability'],
            "24h变化(百分点)":leader['change_pp'],"事件24h成交(美元)":e['volume24'],"预计结束":display_time(e['end'])})
    table(pd.DataFrame(rows),"poly_events",numeric=["隐含概率(%)","24h变化(百分点)","事件24h成交(美元)"])
    options={e['id']:e for e in events}
    ident=st.selectbox("展开事件的全部结果",list(options),format_func=lambda i:options[i]['title'],key="poly_event")
    event=options[ident]
    st.write(event['original'])
    table(pd.DataFrame([{"结果":m['label'],"隐含概率(%)":m['probability'],"24h变化(百分点)":m['change_pp'],
        "24h成交(美元)":m['volume24'],"流动性(美元)":m['liquidity'],"买卖价差(百分点)":m['spread_pp'],
        "来源更新时间":display_time(m['updated_at'])} for m in event['markets']]),"poly_outcomes",numeric=["隐含概率(%)","24h变化(百分点)","24h成交(美元)","流动性(美元)","买卖价差(百分点)"],download=False)
    choices={m['id']:m for m in event['markets']}
    selected=st.multiselect("绘制结果的历史概率",list(choices),default=list(choices)[:min(4,len(choices))],
        format_func=lambda i:choices[i]['label'],key="poly_curves_"+ident,max_selections=6)
    days=st.radio("历史范围",[1,7,30],format_func=lambda d:f"最近{d}天",horizontal=True,key="poly_days")
    fig=go.Figure()
    for market in selected:
        history,error=fetch_history("polymarket",choices[market]['token'],days)
        if error:
            st.caption(f"{choices[market]['label']}：{error}")
        elif history:
            fig.add_scatter(x=pd.to_datetime([r['time'] for r in history],unit='s',utc=True).tz_convert('Asia/Shanghai'),
                           y=[r['probability'] for r in history],name=choices[market]['label'],mode="lines",connectgaps=False)
    if fig.data:
        fig.update_layout(height=340,yaxis=dict(title="市场隐含概率 (%)",range=[0,100]),legend_orientation="h",margin=dict(l=15,r=15,t=20,b=10))
        st.plotly_chart(fig,width="stretch",key="poly_chart")
    with st.expander("结算规则（原文）与来源"):
        rule=st.selectbox("核对哪个结果",list(choices),format_func=lambda i:choices[i]['label'],key="poly_rule_"+ident)
        st.write(choices[rule]['question'])
        st.text(choices[rule]['rules'])
        st.link_button("查看 Polymarket 原事件", "https://polymarket.com/event/"+event['slug'])
    st.caption("价格换算的概率代表该合约市场报价，受成交与价差影响；24h变化单位为百分点。不同事件、重叠阈值和复合事件不能相加。最多读取 economy/finance 各100个活跃事件，非全站穷尽。")


def render_onchain():
    st.subheader("链上市场与 A 股收盘后变化")
    st.write("观察商品、中国资产和海外股票相关永续合约在 A 股收盘后的变化。")
    snap=snapshot("onchain",live=True)
    if not snap:
        return
    all_rows=snap['rows']
    missing=[v[0] for k,v in global_data.CHAIN_ASSETS.items() if k not in {r['symbol'] for r in all_rows}]
    st.caption(f"本次取得 {len(all_rows)}/{len(global_data.CHAIN_ASSETS)} 个观察合约" + ("；暂无报价："+"、".join(missing) if missing else ""))
    cat=st.radio("资产范围",["全部"]+sorted({r['category'] for r in all_rows}),horizontal=True,key="chain_category")
    rows=[r for r in all_rows if cat=="全部" or r['category']==cat]
    st.caption("以下是 Hyperliquid 永续合约标记价，美元计价；中国资产合约价格不等于 A 股人民币现货价格。采集时间是本站请求时间，接口未提供逐合约成交时间。")
    columns={"name":"资产","symbol":"合约","venue":"市场","price":"标记价(美元)","change24":"平台24h涨跌(%)",
        "volume24":"24h成交额(美元)","oi_usd":"持仓名义金额(美元)","oracle_gap":"标记价较预言机(%)"}
    table(pd.DataFrame(rows)[list(columns)].rename(columns=columns),"onchain_markets",numeric=["标记价(美元)","平台24h涨跌(%)","24h成交额(美元)","持仓名义金额(美元)","标记价较预言机(%)"])
    choices={r['symbol']:r for r in rows}
    symbol=st.selectbox("查看收盘后的合约走势",list(choices),format_func=lambda c:stock_label(choices[c]['name'],c),key="chain_detail")
    history,error=fetch_history("onchain",symbol)
    if error:
        st.info(error)
        return
    st.caption(f"A 股收盘锚点 {display_time(history['close_at'])}（北京时间） · 只统计已结束且有成交的15分钟K线。")
    base=history['baseline']
    a,b,c=st.columns(3)
    a.metric("收盘前合约成交基准",fmt(base['close'],4) if base else "—")
    change=global_data.after_close_change(choices[symbol],history)
    b.metric("快照标记价较基准",fmt(change,2,"%"))
    c.metric("已取得K线的成交数量",fmt(history['volume'],2))
    if base:
        st.caption("基准为同一合约在A股收盘前30分钟内最后一根有成交的K线收盘，截止 " + display_time(pd.Timestamp(base['end'],unit='ms',tz='UTC')))
    else:
        st.info("收盘前半小时没有取得同一合约有效成交，暂不计算收盘后涨跌。")
    candles=history['candles']
    if candles:
        points=[]
        for row in candles:
            if points and row['end']-points[-1]['end']>900000:
                points.append(dict(end=points[-1]['end']+900000,close=None))
            points.append(row)
        fig=go.Figure(go.Scatter(x=pd.to_datetime([r['end'] for r in points],unit='ms',utc=True).tz_convert('Asia/Shanghai'),
             y=[r['close'] for r in points],mode="lines+markers",connectgaps=False,name="合约成交价"))
        fig.update_layout(height=320,yaxis_title="合约成交价（美元）",margin=dict(l=15,r=15,t=20,b=10))
        st.plotly_chart(fig,width="stretch",key="chain_chart")
        st.caption("成交数量统计截至 "+display_time(pd.Timestamp(history['through'],unit='ms',tz='UTC'))+"；未取得的时段不补算，非完整市场成交量。")
    st.link_button("Hyperliquid 行情数据说明", "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint")


def render_market_observatory(global_page=False):
    st.title("海外预期与链上市场" if global_page else "资金与基差")
    # A radio renders only the selected section, so unused tabs make no network calls.
    choices={"预测市场":render_polymarket,"链上市场":render_onchain} if global_page else {
        "A股涨跌与融资":render_breadth,"股指期货贴水":render_futures,"宽基ETF成交":render_etf}
    selected=st.radio("查看内容",list(choices),horizontal=True,key="observatory_global" if global_page else "observatory_local")
    choices[selected]()

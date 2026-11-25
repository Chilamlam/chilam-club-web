"""Trace share creation, redemption and financing without guessing the buyer."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import etf_flows as calc
from market_watch import numeric
from market_watch_data import last_close_day
from site_data import load_json, load_csv
from stock_search import matches_stock, stock_label
from investor_journey import candidate_actions


def fmt(v, suffix="", digits=2):
    v=numeric(v)
    return f"{v:,.{digits}f}{suffix}" if v is not None else "—"


def table(frame, key):
    pages=max(1,(len(frame)+19)//20)
    if st.session_state.get(key,1)>pages:
        st.session_state[key]=1
    page=st.selectbox("页码",range(1,pages+1),key=key) if pages>1 else 1
    st.caption(f"共 {len(frame)} 条 · 第 {page}/{pages} 页，每页最多20条")
    st.dataframe(frame.iloc[(page-1)*20:page*20],hide_index=True,width="stretch",
        column_config={c:st.column_config.NumberColumn(format='%.2f') for c in frame
                       if pd.api.types.is_float_dtype(frame[c])})
    st.download_button("下载全部筛选结果",frame.to_csv(index=False).encode('utf-8-sig'),
        file_name=key+'.csv',mime='text/csv',key=key+'_csv')


@st.cache_data(show_spinner=False,max_entries=6)
def calculate(rows, calendar, cutoff, splits):
    return calc.daily_flows(rows,calendar,cutoff,splits)


def comparison_chart(series, start, end, title, key):
    fig=go.Figure()
    for label,frame in series:
        view=frame[(frame.date>start)&(frame.date<=end)]
        # Once a session is missing, cumulative flow is unknown from there on.
        fig.add_scatter(x=pd.to_datetime(view.date),y=view.flow_yi.cumsum(skipna=False),
            mode='lines',connectgaps=False,name=label)
    fig.update_layout(title=title,height=320,yaxis_title='估算净申赎（亿元）',
        legend_orientation='h',margin=dict(l=10,r=10,t=45,b=10),hovermode='x unified')
    st.plotly_chart(fig,width='stretch',key=key)


def render_margin(snap):
    st.subheader("融资余额：哪些行业在减少杠杆")
    rows=snap.get('margin',[])
    if not rows:
        st.info("个股融资明细尚未取得，暂不能按行业比较。")
        return
    days=sorted({r['date'] for r in rows})
    if len(days)<2:
        st.info("目前只有一期融资明细，继续积累后可比较变化。")
        return
    st.caption(f"明细覆盖 {days[0]}—{days[-1]} · 沪深A股，不含ETF、北交所。行业按 {snap.get('industry_date','未知')} 分类快照固定；这是行业篮子比较，不是历史科技成分指数。")
    industries=sorted({r['industry'] for r in rows if r['industry']!='未分类'})
    tech=st.multiselect("科技行业篮子（可修改）",industries,
        default=[n for n in calc.TECH if n in industries],key='flow_tech')
    a,b=st.columns(2)
    start=a.selectbox("融资比较基准日",days[:-1],index=max(0,len(days)-21),key='flow_margin_start')
    end=b.selectbox("融资比较结束日",[d for d in days if d>start],index=len([d for d in days if d>start])-1,key='flow_margin_end')
    results,daily=calc.margin_comparison(rows,tech,start,end)
    show=pd.DataFrame([{"范围":r['group'],"基准余额(亿)":r['start'],"期末余额(亿)":r['end'],
        "区间变化(亿)":r['change'],"区间变化(%)":r['pct'],"已归档区间峰值(亿)":r['peak'],
        "峰值日期":r['peak_date'],"较峰值减少(亿)":-r['from_peak'],
        "基准/期末股票数":f"{r['start_count']}/{r['end_count']}"} for r in results])
    st.dataframe(show,hide_index=True,width='stretch',column_config={c:st.column_config.NumberColumn(format='%.2f') for c in show if '(亿)' in c or '(%)' in c})
    fig=go.Figure()
    for name,group in daily.groupby('group'):
        view=group[(group.date>=start)&(group.date<=end)].set_index('date')
        baseline=view.loc[start,'balance_yi'] if start in view.index else None
        expected=[d for d in load_json('data/trading_calendar.json').get('open_dates',[]) if start<=d<=end]
        view=view.reindex(expected)
        if baseline:
            fig.add_scatter(x=pd.to_datetime(view.index),y=(view.balance_yi/baseline-1)*100,
                name=name,mode='lines',connectgaps=False)
    fig.update_layout(height=300,yaxis_title='相对基准日的融资余额变化 (%)',legend_orientation='h',margin=dict(l=10,r=10,t=15,b=10))
    st.plotly_chart(fig,width='stretch',key='flow_margin_chart')
    st.caption("融资余额减少同时可能来自偿还、卖出还款等，不能据此认定全部筹码卖出。样本数量变化、未分类股票和行业定义也会影响比较。融资余额与ETF估算申赎是不同口径，不能相加当作全市场净流入。")
    with st.expander("核对沪深融资总余额"):
        total=load_csv('data/funding/margin_history.csv')
        if not total.empty:
            total=total.copy();total['date']=total.date.map(calc.norm_date)
            total=total[total.date.between(start,end)].sort_values('date')
            st.dataframe(total.rename(columns={'date':'交易日','balance_yi':'沪深融资余额(亿)','source':'来源'}),hide_index=True,width='stretch')
        st.caption("总余额沿用交易所汇总口径，包含的融资标的与上方纯股票行业篮子不同，披露日可能晚于ETF份额。")


def render_disclosures():
    st.subheader("国家队动向：用公开披露核对")
    st.write("ETF份额增加能确认产品有新增份额，无法单独识别买方。二级市场买入也可以不改变总份额；做市套利、机构配置和个人申赎都可能影响份额。")
    st.caption("以下为已核对的历史公开声明，不是实时持仓或完整公告列表。没有披露的逐日买入金额不作估算归属。")
    for r in calc.DISCLOSURES:
        with st.container(border=True):
            st.write(f"**{r['date']} · {r['institution']} · {r['title']}**")
            st.write(r['fact'])
            st.link_button('查看官方原文',r['url'])
    st.write("观察顺序：先看份额是否连续增加，再看流入集中在哪些指数、是否伴随价格变化；最后用有名称和报告期的持有人披露核对参与者。公开声明只能支持其声明的范围，不能把这24只ETF的新增份额都归给国家队。")


def render_etf_flows():
    st.subheader("宽基ETF份额与申赎")
    st.write("观察增量份额去了哪里，比较双创与其他宽基，再对照融资余额的变化。")
    snap=load_json('data/observatory/etf_flows.json')
    if not snap.get('rows'):
        st.info("交易所份额归档尚未生成，暂不估算申赎。")
        render_disclosures()
        return
    cal=load_json('data/trading_calendar.json')
    cutoff=snap.get('cutoff','')
    frame=calculate(snap['rows'],cal,cutoff,snap.get('splits',{}))
    if frame.empty:
        st.info("没有日期和代码均有效的份额记录。")
        return
    actual=frame[frame.shares.notna()]
    if actual.empty:
        st.info("归档中暂无有效份额，暂不计算申赎。")
        return
    days=sorted(actual.date.unique())
    collected=str(snap.get('collected_at','未知')).replace('T',' ')[:19]
    st.caption(f"交易所份额 {days[0]}—{days[-1]} · 固定观察池 {len(calc.POOL)} 只 · 采集 {collected}（北京时间）。观察池不代表全市场ETF。")
    expected=last_close_day()
    if expected and days[-1]<expected:
        st.info(f"最近已收盘交易日 {expected} 的份额尚未取得，当前截至 {days[-1]}。")
    st.caption("深交所说明：T日晚间份额仅供参考，以T+1日早间更新为准；归档会重新核对近期日期。")
    view=st.radio('观察内容',['份额与申赎','融资结构','公开披露核对'],horizontal=True,key='flow_view')
    if view=='融资结构':
        render_margin(snap)
        return
    if view=='公开披露核对':
        render_disclosures()
        return
    if len(days)<2:
        st.info('需要两个交易日的份额才能计算变化。')
        return
    a,b=st.columns([1,2])
    end=a.selectbox('份额截止日',days[1:],index=len(days)-2,key='flow_end')
    scopes=['全部观察池','双创宽基','其他宽基','自选指数']
    scope=b.radio('观察范围',scopes,horizontal=True,key='flow_scope')
    indices=set(calc.POOL.values())
    if scope=='双创宽基':indices=calc.DUAL
    if scope=='其他宽基':indices=indices-calc.DUAL
    if scope=='自选指数':
        indices=set(st.multiselect('选择指数',sorted(indices),key='flow_indices'))
    symbols=[c for c,i in calc.POOL.items() if i in indices]
    if not symbols:
        st.info('选择至少一个指数。')
        return
    st.caption('双创宽基在本站指创业板指、科创50、科创创业50，三组分别展示。其成分有重叠，ETF金额不等于去重后的底层股票买入额。')
    window=st.radio('比较区间',['1个交易日','5个交易日','20个交易日','60个交易日','自选基准日'],index=2,horizontal=True,key='flow_window')
    eligible=[d for d in days if d<end]
    n={'1个交易日':1,'5个交易日':5,'20个交易日':20,'60个交易日':60}.get(window)
    if n is None:
        start=st.selectbox('份额比较基准日',eligible,index=max(0,len(eligible)-20),key='flow_start')
    else:
        sessions=[d for d in cal.get('open_dates',[]) if d<=end]
        if len(sessions)<=n or sessions[-n-1]<days[0]:
            st.info('当前归档不足这个区间，可缩短区间或自选基准日。')
            return
        start=sessions[-n-1]
    summaries=calc.summarize(frame,symbols,start,end)
    pool=calc.group_flows(frame,symbols)
    complete=len(summaries)==len(symbols) and all(r['flow_yi'] is not None for r in summaries)
    net=sum(r['flow_yi'] for r in summaries) if complete else None
    last=pool[pool.date==end]
    a,b,c=st.columns(3)
    a.metric('观察池区间估算净申赎',fmt(net,' 亿'))
    b.metric('完整可计算ETF',f"{sum(r['flow_yi'] is not None for r in summaries)}/{len(symbols)}")
    c.metric('期末观察池净资产',fmt(last.iloc[0].assets_yi,' 亿') if len(last) else '—')
    st.caption(f'基准收盘 {start} → {end} · 正数为估算净申购，负数为估算净赎回。逐日份额变化×同日单位净值，再对区间求和；不是成交额、总申购额或某家机构买入额。')
    if not complete:
        st.info('部分日期缺少份额、净值或折算核对结果，总额留空；下方仍显示可核对的基金。')
    groups=[];series=[]
    for index in sorted(indices):
        codes=[c for c in symbols if calc.POOL[c]==index]
        items=[r for r in summaries if r['symbol'] in codes]
        valid=len(items)==len(codes) and all(r['flow_yi'] is not None for r in items)
        value=sum(r['flow_yi'] for r in items) if valid else None
        groups.append({'跟踪指数':index,'区间估算净申赎(亿)':value,'有效/观察ETF':f"{sum(r['flow_yi'] is not None for r in items)}/{len(codes)}"})
        series.append((index,calc.group_flows(frame,codes)))
    st.dataframe(pd.DataFrame(groups),hide_index=True,width='stretch',column_config={'区间估算净申赎(亿)':st.column_config.NumberColumn(format='%.2f')})
    comparison_chart(series,start,end,'各指数累计估算净申赎','flow_groups')
    query=st.text_input('搜索观察池ETF',placeholder='名称、代码或拼音首字母',key='flow_query')
    show=[r for r in summaries if matches_stock(query,r['name'],r['symbol'])]
    show.sort(key=lambda r:(r['flow_yi'] is None,-(r['flow_yi'] or 0)))
    shown=pd.DataFrame([{'名称':r['name'],'代码':r['symbol'],'跟踪指数':r['index'],
        '期末份额(亿份)':r['shares_yi'],'区间新增(亿份)':r['change_yi'],'份额变化(%)':r['change_pct'],
        '估算净申赎(亿)':r['flow_yi'],'可计算日/区间':f"{r['observed']}/{r['expected']}",
        '份额/已取区间峰值(%)':r['peak_ratio'],'同长度申赎窗口分位(%)':r['flow_percentile'],
        '可比窗口数':r['comparable_windows']} for r in show])
    table(shown,'flow_etfs')
    st.caption('峰值只比较已取得区间；同长度窗口相互重叠，分位不预测涨跌。不同ETF单位净值不同，跨基金比较优先看金额；新增份额不直接跨指数相加。')
    if show:
        selected=st.selectbox('展开一只ETF',list(r['symbol'] for r in show),format_func=lambda c:stock_label(calc.NAMES[c],c),key='flow_detail')
        detail=frame[(frame.symbol==selected)&frame.date.between(start,end)]
        fig=go.Figure()
        fig.add_scatter(x=pd.to_datetime(detail.date),y=detail.shares_yi,name='份额（亿份）',connectgaps=False)
        fig.add_scatter(x=pd.to_datetime(detail.date),y=detail.nav,name='单位净值（元）',yaxis='y2',connectgaps=False)
        fig.update_layout(height=300,yaxis_title='亿份',yaxis2=dict(title='元',overlaying='y',side='right'),
            legend_orientation='h',margin=dict(l=10,r=10,t=20,b=10))
        st.plotly_chart(fig,width='stretch',key='flow_individual')
        st.caption('份额线反映份数变化，净值线反映每份资产价格；分红也可能影响净值。它们共同决定规模，不能把规模增加全当作申购。')
        candidate_actions(pd.DataFrame([dict(symbol=selected,name=calc.NAMES[selected])]),code_col='symbol',key='flow_research')
        with st.expander('逐日份额与估算明细'):
            raw=detail[['date','shares_yi','change','nav','flow_yi','split','unusual']].copy()
            raw['change']=raw['change']/1e8
            raw=raw.rename(columns={'date':'交易日','shares_yi':'总份额(亿份)','change':'较前日份额(亿份)',
                'nav':'单位净值(元)','flow_yi':'估算净申赎(亿)','split':'已知折算日','unusual':'大幅变动待核对'})
            table(raw.sort_values('交易日',ascending=False),'flow_daily')
    with st.expander('来源、缺失与计算口径'):
        st.write('上交所原字段为万份，深交所下载表按列名识别份/万份；统一换为份。单位净值为未复权净值，只匹配同日。缺失交易日不前填、不拿前一条已取得记录充当前日。')
        st.write('净申赎金额为近似值：申赎对价可以包括证券及现金，不等于等额现金进入A股。拆分折算日、单日份额变化超过50%或净值变化超过40%暂不估算；折算资料未核对成功的基金也不进入金额汇总。这些条件仅拦截待核对数据，不评价交易价值。')
        st.write('固定观察池共24只，包含同一指数的不同产品；它是研究样本，不代表全部宽基、全部ETF或国家队持仓。日期和成员不齐时汇总留空。')
        st.link_button('上交所ETF份额','https://www.sse.com.cn/assortment/fund/etf/list/scale/')
        st.link_button('深交所ETF规模','https://www.szse.cn/market/fund/volume/etf/index.html')
        st.link_button('ETF申购赎回规则','https://etf.sse.com.cn/fund/quertion/')
        if snap.get('errors'):
            st.write('最近采集未取得的部分：')
            for error in snap['errors'][:30]:st.caption(error)

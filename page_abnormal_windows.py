"""Replayable abnormal-window workbench for the initial three case studies."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import abnormal_data as data
import abnormal_windows as calc
from investor_journey import go as navigate, QUOTE_PAGE, WORKBENCH_PAGE
from stock_search import stock_label


def _set_date(key, value):
    st.session_state[key] = value


def _snapshot():
    archive = data.read()
    saved = st.session_state.get('abnormal_snapshot',{})
    if saved.get('updated_at','') > archive.get('updated_at',''):
        archive = saved
    if st.button('更新收盘行情',key='abnormal_refresh'):
        with st.spinner('核对案例股票与对应指数的收盘数据…'):
            archive = data.refresh(archive,data.calendar_dates())
        st.session_state['abnormal_snapshot'] = archive
    if archive.get('errors'):
        st.warning('本次行情未完整更新，保留上次有效数据。')
        with st.expander('更新情况'):
            st.write('；'.join(archive['errors']))
    st.caption(f"行情截至 {archive.get('asof','尚未取得')} · 收盘后更新；盘中价格不计作收盘异动。")
    return archive


def _chart(rows, history, start):
    bars = [r for r in rows if r['date'] >= start]
    states = [s for s in history if s['date'] >= start]
    fig = make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.08,
                        row_heights=[.55,.45],subplot_titles=['价格路径','累计偏离值与严重异动阈值'])
    fig.add_trace(go.Candlestick(x=[r['date'] for r in bars],open=[r['open'] for r in bars],
                                 high=[r['high'] for r in bars],low=[r['low'] for r in bars],
                                 close=[r['close'] for r in bars],name='日K',
                                 increasing_line_color='#d75757',decreasing_line_color='#239b84'),row=1,col=1)
    for label,field,color in [('10日内','ten','#7367d7'),('30日内','thirty','#258ca2')]:
        fig.add_trace(go.Scatter(x=[s['date'] for s in states],y=[s[field]['deviation'] for s in states],
                                 name=label,mode='lines',line=dict(color=color,width=2),
                                 customdata=[[s[field]['start'],s[field]['length']] for s in states],
                                 hovertemplate='%{x}<br>偏离值 %{y:.2f}%<br>起日 %{customdata[0]} · %{customdata[1]}日<extra>%{fullData.name}</extra>'),row=2,col=1)
    for level in [100,200]:
        fig.add_hline(y=level,line_dash='dot',line_color='#999',line_width=1,row=2,col=1,annotation_text=f'{level}%')
    for s in states:
        if s['official_reset']:
            fig.add_vline(x=s['date'],line_dash='dot',line_width=1,line_color='#999')
    fig.update_xaxes(type='category',nticks=7)
    fig.update_yaxes(title_text='元',row=1,col=1)
    fig.update_yaxes(title_text='偏离值 (%)',row=2,col=1)
    fig.update_layout(height=530,xaxis_rangeslider_visible=False,legend_orientation='h',
                      margin=dict(l=10,r=20,t=40,b=10),hovermode='x unified')
    return fig


def _summary(state, events, calendar, rules=None):
    rules=rules or calc.MAIN
    a,b,c = st.columns(3)
    for col,field,threshold,label in [(a,'ten',100,'10日内累计偏离'),(b,'thirty',200,'30日内累计偏离')]:
        item = state[field]
        col.metric(label,f"{item['deviation']:.2f}%")
        col.caption(f"{item['start']} 至 {state['date']} · {item['length']}个交易日")
        margin = threshold-item['deviation']
        col.caption(f"距{threshold}%阈值 {margin:.2f}个百分点" if margin>0 else f"达到{threshold}%计算条件")
    c.metric('10日内同向价格异动 · 推算',f"{state['ordinary_count']} / {rules['count']} 次")
    c.caption(f"每次触及{rules['ordinary']}%后重新计算普通价格异动，重叠区间不重复计次。")
    c.caption('正式披露记录见下方公告；次数不代表剩余涨停数。')
    with st.expander('同向异动计次：发生日期与移出日期'):
        disclosed={e['end']:e for e in calc.known_events(events,state['date']) if e['kind']=='ordinary'}
        details=[]
        for e in state['triggers']:
            notice=disclosed.get(e['end'])
            details.append({'价格推算触及日':e['end'],'移出10日计次窗口':calc.event_expiry(calendar,e['end']),
                            '所选时点的公告记录':notice['published_on']+' 已披露' if notice else '尚无已归档公告'})
        if details:
            st.dataframe(pd.DataFrame(details),hide_index=True,width='stretch')
        else:
            st.write('当前10日计次窗口没有推算出的同向价格异动。')
    if state['serious_reasons']:
        st.warning('收盘计算已触及：'+'；'.join(state['serious_reasons'])+'。需核对正式披露与后续监管状态。')
    elif state['official_reset']:
        st.info('已按已知严重异动事件重新起算。统计窗口重置不表示重点监控结束。')
    else:
        st.caption('上方只计算所选板块上涨方向的价格条件，数值低于阈值不表示已经解除监管。')
    prior = [e for e in calc.known_events(events,state['date']) if e['kind']=='serious']
    if prior:
        e=prior[-1]
        st.write(f"已知严重异动：{e['start']} 至 {e['end']}；公告日期 {e['published_on']}。重点监控是否结束请核对最新正式通知。")


def _forecast(rows, events, dates, state, rules=None):
    rules=rules or calc.MAIN
    st.subheader('下一交易日，哪项条件先接近？')
    st.caption('先滚动窗口，再按假设的指数涨跌幅计算。调整指数假设，观察边界如何变化。')
    index_change=st.number_input('假设对应指数每个交易日涨跌幅 (%)',min_value=-5.0,max_value=5.0,
                                 value=0.0,step=.25,key='abnormal_index_change')
    boundaries=calc.next_boundaries(rows,events,dates[0],index_change,rules)
    nearest=min((b for b in boundaries if b['serious']),key=lambda b:b['trigger_price'])
    if not state['serious_reasons'] and nearest['within_limit']:
        st.write(f"在该指数假设下，{dates[0]}收盘达到 {nearest['trigger_price']:.2f} 元（较所选日 {nearest['trigger_pct']:+.2f}%），将先触及{nearest['rule']}对应的严重异动计算条件。")
    show=[]
    for b in boundaries:
        name=f"3日内{rules['ordinary']}%（若触及为第{b['ordinary_would_be']}次）" if b['rule']=='3日' else f"{b['rule']}内{b['threshold']}%"
        show.append({'条件':name,'最先约束的起日':b['start'],'触及条件的收盘价(元)':b['trigger_price'],
                     '较所选日收盘(%)':b['trigger_pct'],'是否落在次日涨跌停范围':'是' if b['within_limit'] else '否',
                     '对应条件':'严重异动计算条件' if b['serious'] else '普通异动计算条件'})
    st.dataframe(pd.DataFrame(show),hide_index=True,width='stretch',
                 column_config={'触及条件的收盘价(元)':st.column_config.NumberColumn(format='%.2f'),
                                '较所选日收盘(%)':st.column_config.NumberColumn(format='%.2f')})
    st.caption(f"推演日期 {dates[0]} · 价格按0.01元一档向上取首个触及值。它是收盘条件的估算边界，不是盘中限价或可买入价格。")
    st.subheader('试一条后续价格路径')
    a,b=st.columns(2)
    mode=a.selectbox('后续走法',['每天收涨停','先横盘，再每天收涨停','自定每天涨跌幅'],key='abnormal_mode')
    wait,daily=0,float(rules['limit'])
    if mode=='先横盘，再每天收涨停':
        wait=b.selectbox('先横盘几个交易日',[1,2,3],key='abnormal_wait')
    elif mode=='自定每天涨跌幅':
        daily=b.number_input('个股每天涨跌幅 (%)',min_value=-float(rules['limit']),max_value=float(rules['limit']),value=3.0,step=.5,key='abnormal_daily')
    else:
        b.caption(f"涨停价逐日按{rules['limit']}%限制计算，四舍五入到分。")
    if state['serious_reasons']:
        st.info('所选日已触及严重异动计算条件，停止继续推演。可退回前一交易日，或选择已有正式重置记录的日期。')
        return
    future=calc.project(rows,events,dates,daily,index_change,wait,rules)
    table=[]
    for f in future:
        note='；'.join(f['serious_reasons']) or ('新增一次普通价格异动' if f['ordinary_hit'] else '尚未触及所测严重异动条件')
        table.append({'假设日期':f['date'],'走法':'横盘' if f['waiting'] else '涨停' if daily==rules['limit'] else '自定涨跌幅',
                      '假设收盘(元)':f['close'],'当日涨跌(%)':f['change'],'10日偏离(%)':f['ten'],
                      '30日偏离(%)':f['thirty'],'10日内同向次数':f['ordinary_count'],'结果':note})
    if future and future[-1]['serious_reasons']:
        st.write(f"按这条假设路径，第 {len(future)} 个交易日（{future[-1]['date']}）首次触及严重异动计算条件。")
    elif future:
        st.write('这5个假设交易日未触及所测严重异动条件；未对更远日期作推断。')
    st.dataframe(pd.DataFrame(table),hide_index=True,width='stretch',
                 column_config={k:st.column_config.NumberColumn(format='%.2f') for k in ['假设收盘(元)','当日涨跌(%)','10日偏离(%)','30日偏离(%)']})
    st.caption('最多推演5个交易日，首次触及严重异动计算条件即停止；不假定未来公告、监管解除或公司行动。')


def render_abnormal_windows():
    st.header('📐 异动窗口观察')
    st.write('把价格路径和异动窗口放在一起：看当前约束，找窗口滚动节点，再推演后面的走法。')
    st.caption('案例：九阳股份、北纬科技、好当家、创源股份。用于核对普通主板与创业板上涨路径；未覆盖ST、科创板及无涨跌幅限制新股。')
    snap=_snapshot()
    series=snap.get('series',{})
    if not all(symbol in series for symbol in calc.CASES):
        st.info('案例日线尚未取得，请更新收盘行情。')
        return
    ledger=data.read(data.EVENT_PATH)
    calendar=data.calendar_dates()
    left,right=st.columns(2)
    symbol=left.selectbox('选择股票',list(calc.CASES),key='abnormal_symbol',
                          format_func=lambda c:stock_label(calc.CASES[c]['name'],c),
                          help='支持名称、代码和拼音首字母，如 JYGF、BWKJ、HDJ。')
    meta=calc.CASES[symbol]
    rules=calc.market_rules(symbol,meta['name'])
    date_key='abnormal_date_'+symbol
    dates=[r['date'] for r in series[symbol] if meta['start'] <= r['date'] <= snap['asof']]
    if not dates:
        st.info('该案例的观察区间尚无行情。')
        return
    if st.session_state.get(date_key) not in dates:
        st.session_state[date_key]=meta['focus'] if meta['focus'] in dates else dates[-1]
    asof=right.selectbox('复盘截止日（收盘）',dates,key=date_key)
    controls=st.columns(3)
    pos=dates.index(asof)
    controls[0].button('← 前一交易日',disabled=pos==0,key='abnormal_prev',width='stretch',
                        on_click=_set_date,args=(date_key,dates[max(0,pos-1)]))
    controls[1].button('后一交易日 →',disabled=pos==len(dates)-1,key='abnormal_next',width='stretch',
                        on_click=_set_date,args=(date_key,dates[min(len(dates)-1,pos+1)]))
    controls[2].button('回到最新收盘',key='abnormal_latest',width='stretch',on_click=_set_date,args=(date_key,dates[-1]))
    with st.expander('跳到案例节点'):
        for col,date in zip(st.columns(len(meta['points'])),meta['points']):
            col.button(date,disabled=date not in dates,key='abnormal_point_'+symbol+date,
                       on_click=_set_date,args=(date_key,date))
    st.caption(f"正在回看 {asof} 收盘 · {meta['name']} / {meta['benchmark_name']} · 后续实际价格和未到披露日的公告不参与本次计算。")
    try:
        rows=calc.aligned(series[symbol],series[meta['benchmark']],calendar,asof)
        events=ledger.get('events',{}).get(symbol,[])
        history=calc.analyse(rows,events,rules)
    except (ValueError,KeyError,TypeError) as exc:
        st.error(str(exc))
        return
    state=history[-1]
    verified=ledger.get('verified_through','')
    if asof > verified:
        st.warning(f'行情已到 {asof}，正式公告目前只核对至 {verified}；下方为沿用已知事件的价格测算，暂不推演后续路径。')
    _summary(state,events,calendar,rules)
    st.plotly_chart(_chart(rows,history,meta['start']),width='stretch',key='abnormal_chart')
    st.caption('曲线取各有效起点中最大的累计偏离值；图中的下降可能来自窗口滚动或已确认重置，不能直接解释成资金撤退。')
    try:
        next_days=calc.future_dates(calendar,asof,5)
    except ValueError as exc:
        st.info(str(exc))
        next_days=[]
    if next_days and asof <= verified:
        _forecast(rows,events,next_days,state,rules)
        with st.expander('未来5个交易日：哪些旧涨幅移出窗口？'):
            st.caption('这里将个股和指数都保持在所选日收盘价，只观察时间推移的影响。显示的是旧交易日移出，不是监管解除日期。')
            schedule=calc.roll_schedule(rows,events,next_days,rules)
            st.dataframe(pd.DataFrame([{'日期':r['date'],'移出10日窗口':r['expires10'],'移出30日窗口':r['expires30'],
                                       '10日100%边界价':r['boundary10'],'30日200%边界价':r['boundary30']} for r in schedule]),
                         hide_index=True,width='stretch')
    with st.expander('逐日核对价格与涨停记录'):
        path=calc.path_rows(rows,meta['start'],rules)
        st.dataframe(pd.DataFrame(path).rename(columns={'date':'日期','close':'收盘','high':'最高','low':'最低','change':'涨跌幅(%)','event':'当日情况'}),
                     hide_index=True,width='stretch',column_config={'涨跌幅(%)':st.column_config.NumberColumn(format='%.2f')})
    with st.expander('已知公告与计算口径'):
        visible=calc.known_events(events,asof)
        if not visible:
            st.write('所选时点尚无已归档且到披露日的公告。')
        for e in visible:
            label='严重异常波动' if e['kind']=='serious' else '普通异常波动'
            st.markdown(f"- {e['published_on']} · [{label}]({e['source']}) · 统计区间 {e['start']} 至 {e['end']}")
        st.write('累计偏离值 = 个股区间涨幅 − 对应指数区间涨幅，使用期初前收盘价；不把每日涨跌幅直接相加。区间涨幅使用复权价，显示及情景价格使用实际价格。')
        st.write(f"本例检查3日内{rules['ordinary']}%、10日内{rules['count']}次同向价格异动、10日内100%、30日内200%。普通异动次数由价格顺序推算，与已披露公告分开展示；正式重置仅采用已核对记录。")
        st.caption(f"公告人工核对截至 {ledger.get('verified_through','未知')}。行情可继续更新，新增正式重置需补充公告核对；本版未判断换手率异常、下跌方向严重异动及交易所其他认定。")
        st.markdown(f"[查看适用交易规则]({calc.RULE_URLS[meta['exchange']]})")
    a,b=st.columns(2)
    if a.button('查看这只股票的技术图 →',key='abnormal_to_chart',width='stretch'):
        navigate(QUOTE_PAGE,symbol)
    if b.button('打开这只股票的研究档案 →',key='abnormal_to_journal',width='stretch'):
        navigate(WORKBENCH_PAGE,symbol)

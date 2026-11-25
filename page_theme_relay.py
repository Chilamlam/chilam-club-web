"""Turn tomorrow's leader constraint into a dated, editable observation plan."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import abnormal_data as bars
import abnormal_windows as calc
import theme_relay as relay
import theme_relay_data as data
from investor_journey import go, QUOTE_PAGE, WORKBENCH_PAGE
from stock_search import stock_label, matches_stock
from update_schedule import beijing_now


def _pct(value):
    return "待更新" if value is None else f"{value:+.2f}%"


def _boundary(boundaries, date, base):
    item = relay.nearest(boundaries)
    st.markdown(f"**{date}**")
    if not item:
        st.write("假设路径已触及严重异动条件，先核对公告，再做后续预案。")
        return
    st.write(f"收盘达到 **{item['trigger_price']:.2f} 元**，约较{base} **{item['trigger_pct']:+.2f}%**")
    st.caption(f"首先触及：{item['rule']}内{item['threshold']}%" if item['rule']!='3日' else
               f"首先触及：10日内第{item['ordinary_would_be']}次同向价格异动")
    st.caption("在当日涨跌停范围内" if item['within_limit'] else "边界在当日涨跌停范围外，仍须核对指数与公告变化")


def _constraint(leader, series, asof, calendar, prefix):
    st.subheader("龙头明后日的约束")
    rules = calc.market_rules(leader['symbol'],leader['name'])
    events = bars.read(bars.EVENT_PATH).get('events',{}).get(leader['symbol'],[])
    rows = calc.aligned(series.get(leader['symbol'],[]),series.get(rules['benchmark'],[]),calendar,asof)
    a,b=st.columns(2)
    first=a.number_input('假设龙头明日收盘涨跌幅 (%)',min_value=-float(rules['limit']),
                         max_value=float(rules['limit']),value=min(10.,float(rules['limit'])),step=1.,key=prefix+'_first')
    index=b.number_input('假设对应指数每日涨跌幅 (%)',min_value=-5.,max_value=5.,value=0.,step=.5,key=prefix+'_index')
    result=relay.outlook(rows,events,calendar,rules,first,index)
    state=result['state']
    st.caption(f"{leader['name']}收盘 {rows[-1]['close']:.2f}元 · {rules['benchmark_name']} · "
               f"10日内最大累计偏离 {state['ten']['deviation']:.2f}% · 30日内 {state['thirty']['deviation']:.2f}%")
    if result['blocked']:
        st.warning('已触及价格测算条件：'+'；'.join(state['serious_reasons'])+'。后续测算等待正式重置记录。')
    else:
        left,right=st.columns(2,border=True)
        with left:
            _boundary(result['tomorrow'],result['dates'][0],'本日收盘')
        with right:
            if result['path']:
                st.caption(f"若明日收 {result['path'][0]['close']:.2f}元（{result['path'][0]['change']:+.2f}%），再看后日")
            _boundary(result['after'],result['dates'][1],'假设的明日收盘')
        nearest=relay.nearest(result['tomorrow'])
        if nearest and nearest['within_limit']:
            st.info('明日继续加速就可能遇到严重异动约束。今晚先准备候选；次日重点分辨“龙头减速、低位承接”与“整个题材一起退潮”。')
        elif relay.nearest(result['after']) and relay.nearest(result['after'])['within_limit']:
            st.info('按当前假设，约束可能落在后日。可提前记录首板，但仍要等待题材扩散和候选承接。')
        else:
            st.write('当前假设下，明后日未看到涨跌停范围内可触及的严重异动边界；暂不以此作为补涨的时间依据。')
    st.caption('边界是收盘条件估算，不是买入价，也不代表必然停牌。普通异动计次为价格推算；正式公告、重点监控和临时监管认定需另行核对。')
    ledger_date=bars.read(bars.EVENT_PATH).get('verified_through','尚未记录')
    st.caption(f'已归档公告核对截至 {ledger_date}；未自动核对全市场公告。测算假定此后没有新的正式重置或除权事件。')
    with st.expander('查看价格路径、起算区间与适用规则'):
        from page_abnormal_windows import _chart
        st.plotly_chart(_chart(rows,calc.analyse(rows,events,rules),rows[-25]['date']),width='stretch',key=prefix+'_chart')
        boundary_rows=[dict(日期=day,条件=f"{x['rule']}内{x['threshold']}%",起算日=x['start'],触及收盘价=x['trigger_price'])
            for day,items in [(result['dates'][0],result['tomorrow']),(result['dates'][1],result['after'])] for x in items or []]
        if boundary_rows:st.dataframe(pd.DataFrame(boundary_rows),hide_index=True,width='stretch')
        st.write(f"{rules['label']}：{rules['limit']}%涨跌幅限制，普通价格异动{rules['ordinary']}%，10日内{rules['count']}次同向异动；10日100%、30日200%。累计偏离按区间收益之差，逐一起点检查，未来行情不参与计算。")
        st.link_button('交易所规则',calc.RULE_URLS[rules['exchange']])
        for e in calc.known_events(events,asof):
            st.markdown(f"[{e['published_on']} 异动公告]({e['source']}) · {e['start']} 至 {e['end']}")
        st.caption('已归档公告仅作校核，缺少公告不表示未发生监管事件。')
    return result


def _plan(candidate, leader, topic_name, asof, result, prefix, prices_available):
    import decision_workbench as engine
    import research_store as store
    import research_workspace as workspace
    dates=calc.future_dates(bars.calendar_dates(),asof,2)
    relation=candidate['relation']
    st.subheader(f"{candidate['name']}：写进次日预案")
    st.write(f"**{relation['kind']}** · {relation['basis']}")
    st.caption(relation.get('caveat',''))
    turnover=candidate.get('turnover')
    opened=candidate.get('open_num')
    st.caption(f"当日首封 {candidate['first_time']} · 开板 {opened if opened is not None else '待核对'} 次 · "
               f"换手率 {f'{turnover:.2f}%' if turnover is not None else '待核对'} · {candidate['height']}")
    st.link_button('核对关联来源',relation['url'])
    a,b=st.columns(2)
    if a.button('查看候选技术图 →',key=prefix+'_candidate_chart',width='stretch'):go(QUOTE_PAGE,candidate['symbol'])
    if b.button('查看龙头技术图 →',key=prefix+'_leader_chart',width='stretch'):go(QUOTE_PAGE,leader['symbol'])
    base_key=prefix+'_'+candidate['symbol']
    approach=st.radio('打算怎样介入观察',['提前试错（不等龙头触线）','分歧后确认','只观察，不计划参与'],
                      horizontal=True,key=base_key+'_approach')
    plan_key=base_key+'_'+approach
    if approach.startswith('提前'):
        default_trigger=f"在{leader['name']}尚未触及严重异动条件前，先观察{candidate['name']}竞价与开盘的主动性；只有题材仍活跃、候选回踩守住自定价位并再次转强，才按事先限定的试错金额考虑参与。异动边界本身不是买点。"
    elif approach=='分歧后确认':
        default_trigger=f"{leader['name']}减速或分歧时，{candidate['name']}回落有承接并重新转强，题材仍有其他活跃股。核对开盘、回封和可成交性后再判断；涨停名单本身不算触发。"
    else:
        default_trigger=f"只记录{leader['name']}接近约束前后，{candidate['name']}是否领先同身位股票、是否在龙头分歧时保持强度；本预案不参与交易。"
    with st.form(plan_key):
        thesis=st.text_area('为什么提前观察它',value=f"{approach}。观察{topic_name}能否在{leader['name']}接近异动约束时向{candidate['name']}扩散。{relation['basis']}",height=85)
        trigger=st.text_area('什么出现才考虑参与',value=default_trigger,height=85)
        invalid=st.text_area('什么出现就取消预案',value=f"{leader['name']}与相关股票同步走弱，{candidate['name']}冲高回落且承接失败，或关联逻辑被否定；不因股价低就继续等待。",height=85)
        cost=st.text_input('开盘与仓位约束',value='一字板无可成交机会或高开后余下空间不足则放弃；参与前另定买点、止损和可承受金额。')
        review=st.date_input('复核日期',value=__import__('datetime').date.fromisoformat(dates[-1]))
        exit_rule=st.text_input('观察期限 / 未走出预期怎么处理',value=f"观察至{dates[-1]}收盘，未形成相对强势则撤销补涨预期。")
        submitted=st.form_submit_button('保存观察预案',type='primary',width='stretch')
    if submitted:
        try:
            fields=relay.plan_fields(candidate,leader,topic_name,asof,result,
                dict(thesis=thesis,trigger=trigger,invalid=invalid,cost_risk=cost,exit_rule=exit_rule),asof if prices_available and result else '')
            record=engine.new_plan(fields,now=beijing_now().isoformat())
            values=workspace.research(record)
            values['review_on']=review.strftime('%Y%m%d')
            record=workspace.revise(record,values,now=beijing_now().isoformat(),reason='建立题材接力预案')
            # Idempotent repeated submission in this session, while preserving intentional revisions.
            signature=(asof,leader['symbol'],candidate['symbol'],topic_name,thesis,trigger,invalid,cost,exit_rule,review.isoformat())
            prior=st.session_state.get(plan_key+'_saved')
            if prior and prior[0]==signature:
                st.info('这份预案已保存，可到研究档案继续修改和复核。')
            else:
                saved,err=store.save(record)
                if not err:st.session_state[plan_key+'_saved']=(signature,saved['id'])
                st.session_state[store.key()+'_selected_record']=saved['id']
                (st.warning if err else st.success)(store.save_message(saved,err))
        except ValueError as exc:st.error(str(exc))
    st.caption('保存时冻结龙头、候选、题材依据和明后日假设；更新后可在研究档案对照后续收盘表现。此处保存观察，不记作已成交。')
    if st.button('打开研究档案与后续复核 →',key=plan_key+'_research'):go(WORKBENCH_PAGE,candidate['symbol'])


def render_theme_relay():
    st.header(relay.PAGE)
    st.write('先判断龙头明后日的约束，再比较谁可能承接，最后留下可复核的参与与放弃条件。')
    mode=st.radio('工作区',['准备题材预案','核对历史案例'],horizontal=True,key='relay_workspace')
    if mode=='核对历史案例':
        from page_abnormal_windows import render_abnormal_windows
        render_abnormal_windows()
        return
    dates=data.available_dates()
    if not dates:
        st.info('尚未取得完整的收盘涨停池，请先更新。')
        if st.button('更新收盘资料',key='relay_initial'):
            data.refresh();st.rerun()
        return
    focus=st.session_state.pop('relay_focus',{})
    if focus.get('date') in dates:st.session_state['relay_date']=focus['date']
    date_col,leader_col,topic_col=st.columns([1,1.5,1.5])
    asof=date_col.selectbox('复盘依据日',dates,key='relay_date')
    pool=data.load_pool(asof)
    archive=data.load_series()
    if st.session_state.get('relay_prices',{}).get('checked_at','')>archive.get('checked_at',''):
        archive=st.session_state['relay_prices']
    series=archive.get('series',{})
    calendar=bars.calendar_dates()
    now=beijing_now()
    expected=max(d for d in calendar if d<now.date().isoformat() or
                 (d==now.date().isoformat() and now.hour*60+now.minute>=15*60+15))
    st.caption(f"{pool['source']} · {asof}收盘涨停池 {len(pool['stocks'])}只 · 行情、板数和题材均按所选日核对")
    if asof<expected:st.info(f'正在回看{asof}；该日期之后的价格、题材关联不参与候选形成。')
    with st.expander('先扫一眼：哪些连板 / 反复涨停股明后日可能遇到约束'):
        watch=relay.leader_watch(pool,series,bars.read(bars.EVENT_PATH).get('events',{}),calendar)
        if watch['rows']:
            st.dataframe(pd.DataFrame([{'股票':r['name'],'当前路径':r['height'],'约束时间':r['when'],
                '边界收盘价':r['boundary'],'成立假设':r['note'],'当日题材':r['theme']} for r in watch['rows']]),hide_index=True,width='stretch')
            st.caption('扫描收盘涨停池中的连板和反复涨停路径。指数假设持平，后日测算另假设明日涨停；没有列入不代表安全。下方可任选观察龙头。')
        else:st.write('当前可计算样本没有找到上述两日假设内的边界。')
        if watch['missing']:st.caption('未能计算：'+'；'.join(r['name']+'（'+r['reason']+'）' for r in watch['missing']))
    options={r['symbol']:r for r in pool['stocks']}
    leader_options=dict(options)
    for prior_date in [d for d in dates if d<asof][:2]:
        for r in data.load_pool(prior_date).get('stocks',[]):
            if r['symbol'] not in leader_options and relay.stage(r.get('height','')) in ('连板','反复涨停'):
                leader_options[r['symbol']]=dict(r,height=f"本日未收盘涨停 · {prior_date} {r['height']}")
    ordered=sorted(options,key=lambda s:(s!='sz300703',s))
    if focus.get('symbol') in leader_options:st.session_state['relay_leader_'+asof]=focus['symbol']
    leader_symbol=leader_col.selectbox('本轮观察龙头（可自行选择）',sorted(leader_options,key=lambda s:(s!='sz300703',s)),
        format_func=lambda s:stock_label(leader_options[s]['name'],s,leader_options[s]['height']),key='relay_leader_'+asof)
    leader=leader_options[leader_symbol]
    prefix='relay_'+asof+'_'+leader_symbol
    if focus.get('theme'):st.session_state[prefix+'_from_theme']=focus['theme']
    focused_theme=st.session_state.get(prefix+'_from_theme','')
    topics=bars.read(data.TOPIC_PATH).get('topics',[])
    topic=next((t for t in topics if t['leader']==leader_symbol and t['known_on']<=asof),{})
    if focused_theme and focused_theme not in topic.get('tags',[]) and focused_theme!=topic.get('name'):
        topic={}
    topic_name=topic_col.text_input('这次跟踪的题材 / 叙事',value=focused_theme or topic.get('name',leader['theme']),key=prefix+'_topic')
    if leader_symbol not in options:st.caption(leader['height']+'；原题材标签来自该次归档，需复核本日叙事。')
    if topic:
        with st.expander('题材依据与本次研究前提'):
            st.caption(topic['leader_basis'])
            st.link_button('龙头题材依据',topic['leader_url'])
    result=None
    try:result=_constraint(leader,series,asof,calendar,prefix)
    except (ValueError,KeyError,IndexError,TypeError) as exc:st.info(f'龙头明后日约束暂不可计算：{exc}')
    st.subheader('提前比较承接候选')
    st.caption('首板只是本次连板的起点。结合近5/10日涨幅、此前涨停记录判断位置；反包和多次断板单独保留。')
    all_tags=sorted({tag for r in pool['stocks'] for tag in relay.labels(r['theme'])})
    defaults=[t for t in ([focused_theme] if focused_theme else topic.get('tags',relay.labels(leader['theme']))) if t in all_tags]
    tags=st.multiselect('纳入哪些当日题材标签',all_tags,default=defaults,key=prefix+'_tags')
    include=st.checkbox('同时观察有明确说明的叙事扩散假设',value=True,key=prefix+'_extensions')
    members=relay.candidates(pool,leader_symbol,tags,topic,include)
    if topic and not any(r['relation']['kind'] in ('业务关联','同标签观察') and r['stage']=='首板' for r in members):
        st.write('本日尚未找到同标签或已核实直接业务关联的首板。下列延伸对象是在等待确认的假设。')
    with st.expander('从全体涨停股补充自己的候选'):
        manual=st.multiselect('搜索名称、代码或拼音首字母', [s for s in ordered if s!=leader_symbol],
            format_func=lambda s:stock_label(options[s]['name'],s,options[s]['height']),key=prefix+'_manual')
        basis=st.text_input('你认为它们为何可能承接',key=prefix+'_basis',placeholder='写具体业务、情绪联想或盘口关系')
        url=st.text_input('依据链接（留空则引用所选日涨停池，关联仍标为个人假设）',key=prefix+'_url')
        if url and not relay.valid_url(url):st.error('依据链接需为完整的 http / https 地址。')
        if manual and not basis.strip():st.caption('填写关联理由后才会进入本次候选。')
        if basis.strip() and (not url or relay.valid_url(url)):
            for symbol in manual:
                if not any(r['symbol']==symbol for r in members):
                    members.append(dict(options[symbol],relation=dict(kind='个人关联假设',basis=basis.strip(),
                        caveat='由当前用户补充，尚未确认资金承接。',url=url or relay.THS_URL,
                        source_date=asof,known_on=beijing_now().date().isoformat()),stage=relay.stage(options[symbol]['height'])))
    a,b=st.columns(2)
    scope=a.selectbox('候选阶段',['首板优先，保留其他阶段','只看首板','只看反复涨停与连板'],key=prefix+'_scope')
    query=b.text_input('搜索候选',placeholder='名称 / 代码 / 拼音首字母',key=prefix+'_query')
    shown=[r for r in members if matches_stock(query,r['name'],r['symbol']) and
           (scope=='首板优先，保留其他阶段' or (r['stage']=='首板')==(scope=='只看首板'))]
    contexts={r['symbol']:relay.price_context(series,r['symbol'],asof,calendar,r['name']) for r in shown}
    if st.button('更新龙头与当前候选的收盘行情',key=prefix+'_refresh'):
        with st.spinner('更新对应股票和分类指数…'):
            refreshed=data.refresh_prices(archive,[leader]+members,asof)
            st.session_state['relay_prices']=refreshed
        st.rerun()
    if archive.get('errors'):
        with st.expander('最近更新未完成的项目'):st.write('；'.join(archive['errors']))
    if not shown:
        st.info('当前没有符合条件的候选。可调整标签或自行补充关联，不需要为了凑名单强选股票。')
        return
    frame=[]
    for r in shown:
        p=contexts[r['symbol']]
        frame.append({'股票':r['name'],'代码':r['symbol'],'阶段':r['height'],
            '近5日':_pct(p.get('five')),'近10日':_pct(p.get('ten')),
            '前19日涨停次数':str(p['prior_limits']) if p.get('prior_limits') is not None else '待核对',
            '关联性质':r['relation']['kind'],'首次封板':r['first_time'],'当日原归因':r['theme']})
    st.dataframe(pd.DataFrame(frame),hide_index=True,width='stretch')
    st.caption('按首板优先、首次封板时间排列。候选之间的承接关系需在下一交易日观察。')
    st.markdown('**明日按这三种表现处理**')
    st.dataframe(pd.DataFrame([
        {'出现的表现':'龙头减速，候选相对走强，题材仍有承接','预案处理':'继续核对候选回踩、转强与成交机会；仅满足自己的触发条件才考虑参与'},
        {'出现的表现':'龙头与相关股一起走弱，首板普遍兑现','预案处理':'取消补涨预期，低位不等于有承接'},
        {'出现的表现':'龙头继续加速，候选没有独立强度','预案处理':'保留观察，不把龙头触及边界当作候选的买入信号'}]),hide_index=True,width='stretch')
    chosen=st.selectbox('选择候选，核对逻辑并准备预案',shown,
        format_func=lambda r:stock_label(r['name'],r['symbol'],r['height']),key=prefix+'_candidate')
    _plan(chosen,leader,topic_name,asof,result,prefix,contexts[chosen['symbol']].get('available',False))


def render_relay_followup(evidence, key):
    with st.expander('原题材预案与后续表现',expanded=True):
        st.write(f"{evidence['topic']} · 原观察龙头 {evidence['leader_name']} · 依据日 {evidence['date']}")
        original=evidence.get('outlook')
        if original and not original.get('blocked'):
            boundary=relay.nearest(original.get('tomorrow'))
            if boundary:st.caption(f"当时的明日边界 {boundary['trigger_price']:.2f}元；指数假设每日{original['index_change']:+.2f}%。原假设保留，不随新行情改写。")
        archive=data.load_series()
        if st.session_state.get('relay_prices',{}).get('checked_at','')>archive.get('checked_at',''):
            archive=st.session_state['relay_prices']
        series=archive.get('series',{})
        rows=relay.follow_up(evidence,series,bars.calendar_dates())
        st.dataframe(pd.DataFrame([{'身份':r['role'],'代码':r['symbol'],'后续交易日':r['date'],
            '收盘':r['close'],'较原复盘日':_pct(r['change'])} for r in rows]),hide_index=True,width='stretch')
        st.caption('价格对照不能单独判定补涨成立。请在下方记录当时的触发是否出现、取消条件是否出现；没有成交就保留观察记录。')

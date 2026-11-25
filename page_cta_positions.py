"""CTA-related public positions with explicit denominators and publication lag."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import cta_positions as calc
import cta_data as data
from site_data import load_json
from update_schedule import beijing_now


def fmt(value, suffix='', digits=2):
    value = calc.numeric(value)
    return f'{value:,.{digits}f}{suffix}' if value is not None else '—'


def time_text(value):
    try: return pd.Timestamp(value).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError): return '未知'


@st.cache_data(ttl=3600, max_entries=2, show_spinner=False)
def fetch_recent():
    return data.fetch()


def snapshot():
    old = load_json('data/observatory/cta_positions.json')
    # Preserve a live success if the next check fails before the daily archive catches up.
    saved = st.session_state.get('cta_last_good')
    if saved and (saved.get('checked_at') or '') > (old.get('checked_at') or ''):
        old = saved
    if st.button('检查新报告', key='cta_refresh'):
        fetch_recent.clear()
    with st.spinner('核对CFTC最新公开报告…'):
        fresh = fetch_recent()
    result = data.merge(old, fresh)
    st.session_state['cta_last_good'] = result
    if result.get('errors'):
        st.warning('部分来源本次未更新成功，保留其上次有效数据；各合约的持仓日期仍单独显示。')
        with st.expander('本次更新情况'):
            for error in result['errors']: st.write(error)
    st.caption(f"最近查新 {time_text(result.get('checked_at'))}（北京时间） · 持仓按周披露；访问时自动查新，缓存1小时，页面保持打开时每小时再查。")
    return result


def draw(dataframe, codes, start, end, dollar, title, key):
    fig = go.Figure()
    palette = ['#6366f1', '#d49c18', '#15a68c', '#e05a6b', '#2c8bd2', '#b27cc8', '#7b8f4b']
    for i, code in enumerate(codes):
        points = calc.chart_points(dataframe, code, start, end, dollar)
        fig.add_scatter(x=[p['date'] for p in points], y=[p['value'] for p in points],
                        name=calc.MARKETS[code]['name'], mode='lines', connectgaps=False,
                        line=dict(color=palette[i % len(palette)], width=2),
                        hovertemplate='%{x}<br>%{y:.2f}%<extra>%{fullData.name}</extra>')
    fig.add_hline(y=0, line_color='#999', line_width=1)
    fig.update_layout(title=title, height=370, yaxis_title='净持仓 / 该合约总未平仓量 (%)',
                      margin=dict(l=15,r=15,t=45,b=10), legend_orientation='h', hovermode='x unified')
    st.plotly_chart(fig,width='stretch',key=key)


def show_table(rows, key):
    view = pd.DataFrame(rows)
    st.dataframe(view, hide_index=True, width='stretch',
                 column_config={c:st.column_config.NumberColumn(format='%.2f') for c in view
                                if '(%)' in c or '(百分点)' in c})
    st.download_button('下载表格',view.to_csv(index=False).encode('utf-8-sig'),
                       file_name=key+'.csv',mime='text/csv',key=key+'_download')


@st.fragment(run_every='1h')
def render_cta_positions():
    st.subheader('CTA观察 · 公开基金持仓')
    st.write('观察美元和金属的持仓方向，区分增多、减空与减多、增空，再核对它处于怎样的历史位置。')
    st.info('本页使用CFTC实际申报持仓：商品看管理基金，外汇看杠杆基金，两类都包含CTA及其他基金。曲线的百分比分母是该合约总未平仓量，并非CTA组合配置比例。')
    snap = snapshot()
    now = beijing_now()
    source = calc.frame(snap.get('rows',[]), now.date().isoformat())
    if source.empty:
        st.info('尚未取得有效持仓记录，暂不绘图。')
        st.link_button('CFTC官方报告',calc.COT_URL)
        return
    release = calc.release_state(snap.get('schedule',{}), now)
    if release['next']:
        st.caption(f"下一次官方计划发布：{time_text(release['next'])}（北京时间，节假日顺延已计入；最终以官方公告为准）。")
    else:
        st.caption('未取得本年度剩余发布时间表，请以官方日历为准。')
    if release['expected']:
        missing = [m['name'] for code,m in calc.MARKETS.items()
                   if not len(source[(source.code==code)&source.date.ge(release['expected'])])]
        if missing:
            st.warning(f"按官方日历，最近计划披露的持仓日为 {release['expected']}；尚未取得：{'、'.join(missing)}。下方显示已有历史记录。")
    scope = st.radio('观察市场',['金属：黄金、铜、白银','美元与主要货币'],horizontal=True,key='cta_scope')
    family = 'disagg' if scope.startswith('金属') else 'tff'
    codes = [code for code,meta in calc.MARKETS.items() if meta['family']==family]
    section = source[source.code.isin(codes)]
    if section.empty:
        st.info('该类持仓尚无有效记录。')
        return
    a,b = st.columns(2)
    dates = sorted(section.date.unique())
    end = a.selectbox('持仓截止日',dates,index=len(dates)-1,key='cta_end_'+family)
    window = b.selectbox('曲线与分位观察区间',['近1年','近3年','近5年','全部已取得历史'],index=1,key='cta_window')
    years = {'近1年':1,'近3年':3,'近5年':5,'全部已取得历史':0}[window]
    start = (pd.Timestamp(end)-pd.DateOffset(years=years)).strftime('%Y-%m-%d') if years else calc.START
    dollar = family == 'tff'
    if dollar:
        dollar = st.radio('外汇方向口径',['统一美元方向','原合约方向'],horizontal=True,key='cta_fx_basis')=='统一美元方向'
        selected = st.multiselect('绘制哪些合约',codes,default=['098662'],format_func=lambda c:calc.MARKETS[c]['name'],key='cta_fx_codes')
        st.caption('美元指数单独反映ICE美元指数期货。统一美元方向时，欧元、日元等外币合约的净持仓取相反数：正值偏多美元，负值偏空美元。各条线分别观察，不合成为全市场美元仓位。' if dollar
                   else '原合约方向：正值代表净多该货币或美元指数，负值代表净空。不同货币的合约手数和规模不能直接相加。')
    else:
        selected = st.multiselect('绘制哪些金属',codes,default=codes,format_func=lambda c:calc.MARKETS[c]['name'],key='cta_metal_codes')
        st.caption('正值为管理基金净多该金属，负值为净空。黄金、铜、白银分别使用各自合约总未平仓量作为分母。')
    status = snap.get('sources',{}).get(family,{})
    st.caption(f"报告分类：{calc.FAMILIES[family]['name']} · 仅期货，不含期权 · 该来源最近成功查新 {time_text(status.get('checked_at'))}。")
    rows = calc.summaries(source,codes,end,years,dollar)
    selected_rows = [r for r in rows if r['code'] in selected]
    if selected_rows:
        for offset in range(0,len(selected_rows),3):
            for col,r in zip(st.columns(min(3,len(selected_rows)-offset)),selected_rows[offset:offset+3]):
                col.metric(r['name']+' · 净持仓占比',fmt(r.get('net_pct'),'%'),
                           (fmt(r['week_pp'],' 个百分点')+' / 一周') if r.get('week_pp') is not None else None,
                           delta_color='off')
        draw(source,selected,start,end,dollar,'美元方向持仓' if dollar else '基金净持仓占比', 'cta_curves')
    else:
        st.info('选择至少一个合约即可绘制曲线，下方保留完整观察名单。')
    shown=[]
    for r in rows:
        shown.append({'合约':r['name'],'所选日':end,'该合约最近持仓日':r['latest'],
                      '净持仓占比(%)':r.get('net_pct'),'一周变化(百分点)':r.get('week_pp'),
                      '四周变化(百分点)':r.get('month_pp'),'所选区间分位(%)':r.get('percentile'),
                      '分位样本数':r.get('samples'),'样本起日':r.get('history_start')})
    show_table(shown,'cta_summary')
    st.caption('一周、四周变化分别核对7天、28天前的同一合约；缺失时留空。分位按当前所选方向和截止日计算，数值越大只表示相对历史更偏多，不自动代表反转或买卖时点。')
    selected_code = st.selectbox('拆开看多空变化',codes,format_func=lambda c:calc.MARKETS[c]['name'],key='cta_detail_'+family)
    row = next(r for r in rows if r['code']==selected_code)
    if row['available']:
        st.markdown(f"**{row['name']} · {end} 原合约持仓**")
        a,b,c = st.columns(3)
        a.metric('多头（手）',fmt(row['long'],digits=0),fmt(row['long_change'],digits=0)+' / 一周' if row['long_change'] is not None else None,delta_color='off')
        b.metric('空头（手）',fmt(row['short'],digits=0),fmt(row['short_change'],digits=0)+' / 一周' if row['short_change'] is not None else None,delta_color='off')
        c.metric('套利 / 跨期（手）',fmt(row['spreading'],digits=0))
        if row['long_change'] is not None:
            st.write(f"原合约本周：多头{'增加' if row['long_change']>=0 else '减少'} {abs(row['long_change']):,} 手，空头{'增加' if row['short_change']>=0 else '减少'} {abs(row['short_change']):,} 手。结合两者判断变化来自哪一侧。")
        st.caption('套利 / 跨期持仓单独披露，不重复计入方向性多空。基金类别包含不同策略，不能把每次变化归因于CTA趋势交易。')
    else:
        st.info(f"该合约未取得 {end} 的记录，不沿用上期值。")
    with st.expander('逐期明细与下载'):
        history = source[(source.code==selected_code)&source.date.between(start,end)].copy().sort_values('date',ascending=False)
        total = len(history)
        pages = max(1,(total+19)//20)
        key = 'cta_history_page'
        if st.session_state.get(key,1)>pages: st.session_state[key]=1
        page = st.selectbox('明细页码',range(1,pages+1),key=key)
        view = history.drop(columns='code').rename(columns={'date':'持仓日','long':'多头(手)','short':'空头(手)','spreading':'套利(手)','oi':'总未平仓(手)','net':'原合约净持仓(手)','net_pct':'原合约净持仓占比(%)'})
        st.caption(f'共{total}期 · 每页20期；下载包含所选区间全部记录。')
        st.dataframe(view.iloc[(page-1)*20:page*20],hide_index=True,width='stretch',column_config={'原合约净持仓占比(%)':st.column_config.NumberColumn(format='%.2f')})
        st.download_button('下载逐期原合约明细',view.to_csv(index=False).encode('utf-8-sig'),file_name='cftc_'+selected_code+'.csv',mime='text/csv',key='cta_raw_download')
    with st.expander('如何使用、数据来源与更新口径'):
        st.write('先看净持仓方向，再拆开多头与空头的增减，最后对照同一口径的历史位置。它帮助研究拥挤与仓位变化；价格可能在一边倒的持仓下继续原有趋势。')
        st.write('百分比 =（该类基金多头－空头）÷该合约全市场未平仓量×100。分母改变也会带动比例变化；它不是基金资金配置比例，手数变化也不是美元净流入。')
        st.write('CFTC通常在美国东部周五15:30披露截至周二的持仓，北京时间通常为周六03:30或04:30；假日按官方日历顺延。网站在每日跑批归档，同时打开页面查新，缓存1小时。检查按钮可立即重查；上游失败时保留历史并标明日期。')
        st.write('本站从2012年11月开始回填，历史可能含官方后续修订；图表横轴是持仓日期，不是当时公开可见日期，不能直接作为无前视偏差的回测输入。缺周不补0、不前填。分位采用小于当前值的样本数加半数并列样本，再除以样本总数；只使用截止日及以前的数据。')
        for label,url in [('官方COT报告',calc.COT_URL),('官方发布时间表',calc.SCHEDULE_URL),('管理基金分类说明',calc.MM_URL),('杠杆基金分类说明',calc.TFF_URL)]:
            st.link_button(label,url)

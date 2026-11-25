"""One source and one definition shared by rotation and theme tracking."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import theme_activity as ta
import market_insights as mi
from ladder_themes import SOURCES
from site_data import load_json, load_csv
from stock_search import matches_stock, stock_label
from investor_journey import candidate_actions, go as navigate


def load_context(asof=None):
    ladder=load_json("data/limit_ladder.json")
    asof=ta.norm_date(asof or ladder.get("date"))
    calendar=load_json("data/trading_calendar.json")
    archives={d:load_json(f"data/theme_history/{d}.json") for d in ta.trading_days(calendar,asof,20)}
    if ta.norm_date(ladder.get("date"))==asof:
        archives[asof]=dict(date=asof,ladder=ladder,attribution=load_json("data/limit_themes.json"))
    return asof,calendar,archives


def show_table(frame,key):
    if frame.empty:
        st.info("当前条件下没有记录。")
        return
    pages=(len(frame)+19)//20
    page_key=key+"_page"
    if st.session_state.get(page_key,1)>pages:
        st.session_state[page_key]=1
    page=st.selectbox("页码",range(1,pages+1),key=page_key) if pages>1 else 1
    st.caption(f"共 {len(frame)} 条 · 第 {page}/{pages} 页，每页最多20条")
    st.dataframe(frame.iloc[(page-1)*20:page*20],hide_index=True,width="stretch")


def render_activity(*,key,asof=None,calendar=None,archives=None,prices=None,tracking=False):
    if archives is None:
        asof,calendar,archives=load_context(asof)
    focus=st.session_state.pop("theme_focus",{}) if tracking else {}
    if focus.get("source") in ta.PROVIDERS:
        st.session_state[key+"_source"]=focus['source']
        st.session_state[key+"_minimum"]=1
        st.session_state[key+"_query"]=""
        st.session_state[key+"_scope"]="近10日出现"
    source=st.radio("题材来源",list(ta.PROVIDERS),format_func=ta.PROVIDERS.get,horizontal=True,key=key+"_source")
    days=ta.trading_days(calendar,asof)
    available=[d for d in reversed(days) if (archives.get(d) or {}).get("date")]
    if not available:
        st.info("尚未取得按交易日归档的涨停归因。")
        return
    if focus.get('date') in available:
        st.session_state[key+"_date"]=focus['date']
    selected_date=st.selectbox("题材日期",available,key=key+"_date")
    result=ta.activity(archives,calendar,selected_date,source)
    current=result['current']
    st.caption(f"{result['source_name']} · 交易日 {selected_date} · 已归因 {current['matched']}/{current['total']} 只归档涨停股")
    st.write("按当日涨停原因的原始题材标签归组。一只股票可有多个标签；这里统计已归因涨停股，不是整个概念板块的涨幅。")
    if current['matched']<current['total']:
        st.caption("部分涨停股尚无题材归因。表中为已取得记录的数量，未找到标签不作0处理；不计算归因不完整日期之间的数量变化。")
    previous=result['previous']
    if previous and previous['available'] and not previous['complete']:
        st.caption(f"前一交易日 {previous['date']} 已归因 {previous['matched']}/{previous['total']} 只；前日数量为已取得记录，较前日变化暂不计算。")
    if not current['available']:
        st.info("该来源在所选日期暂无有效归因，可切换来源或日期。")
        return
    rows=result['rows']
    scope=st.radio("题材范围",["本日与前日","本日有涨停","近10日出现"],horizontal=True,key=key+"_scope")
    if scope=="本日与前日":
        rows=[r for r in rows if (r['count'] or 0)>0 or (r['previous'] or 0)>0]
    elif scope=="本日有涨停":
        rows=[r for r in rows if (r['count'] or 0)>0]
    a,b,c=st.columns([2,1,1])
    query=a.text_input("搜索题材",placeholder="名称或拼音首字母，如 rxjqr",key=key+"_query")
    minimum=b.selectbox("单日至少几只涨停",[2,1,3,5],format_func=lambda n:f"至少{n}只",key=key+"_minimum",
        help="在所选范围内，至少有一天达到该只数。这样前日活跃、本日没有涨停的题材也能留下。")
    ordering=c.selectbox("排序",["本日涨停数","较前日增加","较前日减少","最高连板","连续出现"],key=key+"_order")
    def peak(r):
        return r['peak'] if scope=="近10日出现" else max(r['count'] or 0,r['previous'] or 0) if scope=="本日与前日" else r['count'] or 0
    rows=[r for r in rows if peak(r)>=minimum and matches_stock(query,r['theme'],"")]
    field={"本日涨停数":"count","较前日增加":"change","较前日减少":"change","最高连板":"height","连续出现":"streak"}[ordering]
    sign=1 if ordering=="较前日减少" else -1
    rows.sort(key=lambda r:(r[field] is None,sign*(r[field] or 0),-(r['count'] or 0),r['theme']))
    if not rows:
        st.info("当前筛选没有题材，可降低只数条件或清空搜索。")
        return
    def count(v): return "—" if v is None else str(v)
    show_table(pd.DataFrame([{"题材":r['theme'],"本日已归因涨停":count(r['count']),"前日同标签":count(r['previous']),
        "较前日变化":f"{r['change']:+d}" if r['change'] is not None else "—",
        "最高连板":count(r['height']),"近10日出现/可核对":f"{r['appearances']}/{r['observed']}",
        "连续出现":r['streak'],"连板数靠前的股票":r['leaders']} for r in rows]),key+"_themes")
    if not tracking:
        st.subheader("题材每天出现在哪里")
        chosen=rows[:15]
        fig=go.Figure(go.Heatmap(z=[r['counts'] for r in chosen],x=result['days'],y=[r['theme'] for r in chosen],
            colorscale="OrRd",hoverongaps=False,colorbar=dict(title="已归因涨停数"),
            hovertemplate="%{x}<br>%{y}：%{z}只<extra></extra>"))
        fig.update_layout(height=max(280,len(chosen)*28),margin=dict(l=5,r=5,t=10,b=15),
                          xaxis=dict(type="category"),yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig,width="stretch",key=key+"_matrix")
        st.caption("展示当前筛选前15个题材。空白表示归档缺失或该日归因不完整且未找到该标签；0只在完整归因中确认。")
    names=[r['theme'] for r in rows]
    wanted=focus.get('theme')
    selector=key+"_pick"
    if wanted in names:
        st.session_state[selector]=wanted
    selected=st.selectbox("查看题材对应股票",names,format_func=lambda n:stock_label(n,""),key=selector)
    row=next(r for r in rows if r['theme']==selected)
    if not tracking and st.button("继续跟踪这个题材 →",key=key+"_follow",width="stretch"):
        st.session_state['theme_focus']=dict(theme=selected,source=source,date=selected_date)
        navigate("🧩 题材跟踪")
    changes=ta.member_changes(result,selected)
    st.subheader(selected)
    if changes['previous_date']:
        st.caption(f"与 {changes['previous_date']} 的同来源标签对照：两日均出现 {len(changes['retained'])} 只；"
                   f"本日新增记录 {len(changes['new'])} 只；前日出现、本日未归入 {len(changes['exited'])} 只。"
                   +( "" if changes['known'] else "部分股票归因缺失，变化仅指已取得记录。"))
    else:
        st.caption("缺少前一交易日的同来源归因，暂不比较成员变化。")
    if row['members']:
        show_table(pd.DataFrame([{"名称":m['name'],"代码":m['symbol'],"连板数":count(m['boards']),
            "首次封板":m['first_time'],"当日原始题材":m['theme'],"来源":m['source']} for m in row['members']]),key+"_members")
    else:
        st.info("本日完整归因中没有该标签的涨停股。" if current['complete'] else "本日已取得归因中没有该标签，尚有股票归因缺失。")
    candidate_actions(pd.DataFrame(row['members']),key=key+"_research",code_col="symbol")
    if tracking:
        if changes['exited']:
            with st.expander("前日出现、本日未归入的股票"):
                show_table(pd.DataFrame([{"名称":m['name'],"代码":m['symbol'],"今日仍在涨停池":m['today_limit'],
                    "前日题材":m['theme']} for m in changes['exited']]),key+"_exited")
                st.caption("未归入可能因为没有涨停、原因标签改变或归因缺失，不直接判定题材失效。")
                candidate_actions(pd.DataFrame(changes['exited']),key=key+"_prior_research",code_col="symbol")
        projection={d:s['projection'] for d,s in result['snapshots'].items() if s['available']}
        detail=mi.theme_leader_followers(selected,projection,current['projection']['ladder'],
            current['projection']['attribution'],prices if prices is not None else load_csv("data/scorecard/prices.csv"),calendar,selected_date)
        if detail['leaders']:
            st.write("前日已归因股票的后续表现")
            show_table(pd.DataFrame([{"前日身份":label,"有效价格/样本":f"{group['n']}/{group['base']}",
                "今日中位涨跌":f"{group['median']:+.2f}%" if group['median'] is not None else "—"}
                for label,group in (("最高连板样本",detail['leaders']),("其余涨停样本",detail['followers']))]),key+"_outcome")
        st.caption(detail['note'])
    with st.expander("来源、覆盖与统计口径"):
        st.write("标签直接来自所选来源当日涨停归因，按完整标签匹配，不把东财概念名称套到同花顺股票上，也不合并近义词或猜测主线。连板数来自同日涨停归档，连板数靠前不代表已确认龙头。")
        st.write("首次补齐的历史归因来自来源按日期返回的数据，不代表本站当时已保存。缺少归因不补0，不跨缺失交易日累计连续出现。")
        show_table(pd.DataFrame([{"交易日":d,"已归因":s['matched'],"归档涨停股":s['total'],
            "归因完整":s['complete']} for d,s in reversed(list(result['snapshots'].items()))]),key+"_coverage")
        st.link_button(result['source_name'], SOURCES[source]['url'])

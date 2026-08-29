import streamlit as st
import pandas as pd
import os
import json
import plotly.graph_objects as go
import plotly.express as px
from page_fibonacci import render_fibonacci_chart
import streamlit.components.v1 as components
from config_gurus import GURUS
from page_core_driver import render_core_driver_page
from page_macro_erp import render_macro_erp_page
from page_watchlist import render_watchlist_page
from page_live_quote import render_live_quote_page
from ui_compat import image_stretch
import auth

# ================= 1. 基础配置 (必须放在最前面) =================
st.set_page_config(
    page_title="Chilam Club - 投资驾驶舱", 
    page_icon="🚀", 
    layout="wide"
)

def inject_ga_safe():
    GA_ID = "G-1HFTXNLL20"
    ga_code = f"""
    <script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', '{GA_ID}');
    </script>
    """
    components.html(ga_code, width=10, height=10)

def inject_ga():
    GA_ID = "G-1HFTXNLL20"
    try:
        import streamlit
        index_path = os.path.join(os.path.dirname(streamlit.__file__), "static", "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
        if GA_ID not in html:
            ga_script = f"""
            <script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>
            <script>
              window.dataLayer = window.dataLayer || [];
              function gtag(){{dataLayer.push(arguments);}}
              gtag('js', new Date());
              gtag('config', '{GA_ID}');
            </script>
            </head>
            """
            new_html = html.replace("</head>", ga_script)
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(new_html)
    except Exception as e:
        pass

inject_ga_safe()
inject_ga()

# ================= 2. 核心辅助函数 =================

@st.cache_data(ttl=600)
def load_data(path):
    if not os.path.exists(path): return None
    try: return pd.read_csv(path)
    except: return None

@st.cache_data(ttl=600)
def load_json(path):
    if not os.path.exists(path): return None
    try:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except: return None

def format_rps_show(df, rps_col='RPS_50', chg_col='rps_50_chg'):
    if df is None or df.empty: return df
    if chg_col not in df.columns:
        df[f'{rps_col}_Show'] = df[rps_col].map(lambda x: f"{x:.1f}")
        return df
    def _fmt(row):
        val = row[rps_col]
        chg = row[chg_col]
        if chg == 999: return f"{val:.1f} 🆕"
        elif chg > 0: return f"{val:.1f} 🔺{abs(chg):.1f}"
        elif chg < 0: return f"{val:.1f} 🔻{abs(chg):.1f}"
        return f"{val:.1f} -"
    df[f'{rps_col}_Show'] = df.apply(_fmt, axis=1)
    return df

def get_market_snapshot_local():
    df = load_data("data/market_snapshot.csv")
    if df is not None and not df.empty:
        return df
    return pd.DataFrame()


# ================= 3. 页面渲染模块 =================
def render_market_dashboard():
    st.header("🛸 全市场情绪看板")
    st.caption("数据驱动 + AI 逻辑分析 | 每日收盘18：00后更新")
    
    df_index = load_data("data/index_history.csv")
    df_sent = load_data("data/market_sentiment.csv")
    df_sector = load_data("data/sector_hot.csv")
    ai_data = load_json("data/ai_market_analysis.json")

    if ai_data:
        st.markdown("### 🤖 AI 首席策略官复盘")
        
        # 一键导出 Markdown / 研报卡片
        report_md = f"""# Chilam Club 投资日记复盘 ({ai_data.get('date', '今日')})

## 一、 主线与盘口逻辑
{ai_data.get('main_logic', '')}

## 二、 核心涨停龙头研判
"""
        if 'limit_reasons' in ai_data:
            for r in ai_data['limit_reasons']:
                report_md += f"- **{r.get('name')}**: {r.get('reason')}\n"

        if os.path.exists("data/market_sentiment.csv"):
            df_s = pd.read_csv("data/market_sentiment.csv")
            if not df_s.empty:
                last_s = df_s.iloc[-1]
                report_md += f"\n## 三、 市场多空情绪\n- 上涨家数: {int(last_s.get('up_count', 0))} | 下跌家数: {int(last_s.get('down_count', 0))}\n- 全市场成交额: {last_s.get('total_amount_yi', 0)} 亿元\n"

        st.download_button(
            label="📄 一键导出今日复盘研报 (Markdown)",
            data=report_md.encode('utf-8'),
            file_name=f"chilam_market_daily_{ai_data.get('date', 'today')}.md",
            mime="text/markdown"
        )

        col_summary, col_divergence = st.columns([1.2, 1])
        
        with col_summary:
            st.info(f"**【主线逻辑】**：\n\n {ai_data.get('main_logic', '分析中...')}")
            with st.expander("🔍 龙头涨停揭秘 (点击右侧选择挖掘补涨)", expanded=True):
                if 'limit_reasons' in ai_data:
                    df_reason = pd.DataFrame(ai_data['limit_reasons'])
                    st.dataframe(
                        df_reason, 
                        column_config={"name": "股票名称", "reason": "涨停逻辑分析"},
                        hide_index=True, use_container_width=True
                    )

        with col_divergence:
            st.subheader("🕸️ 概念发散 (寻龙诀)")
            if 'limit_reasons' in ai_data and len(ai_data['limit_reasons']) > 0:
                df_reason = pd.DataFrame(ai_data['limit_reasons'])
                if 'name' in df_reason.columns:
                    stock_list = df_reason['name'].tolist()
                    selected_stock = st.selectbox("🎯 选择龙头股，挖掘同源补涨标的：", stock_list)
                    
                    if selected_stock:
                        df_snap = get_market_snapshot_local()
                        
                        if not df_snap.empty:
                            stock_row = df_snap[df_snap['name'] == selected_stock]
                            if not stock_row.empty:
                                industry = stock_row['industry'].values[0]
                                
                                if pd.notna(industry) and industry != '':
                                    st.markdown(f"**📍 共通属性解析**: 核心资金正在攻击 `[{industry}]` 板块")
                                    valid_df = df_snap[(df_snap['industry'] == industry) & (df_snap['close'] > 0)].copy()
                                    
                                    if not valid_df.empty:
                                        valid_df['close'] = pd.to_numeric(valid_df['close'], errors='coerce')
                                        valid_df['circ_mv'] = pd.to_numeric(valid_df.get('circ_mv', 999999999), errors='coerce')
                                        valid_df['amount'] = pd.to_numeric(valid_df['amount'], errors='coerce')
                                        
                                        lowest_p = valid_df.sort_values('close').iloc[0]
                                        largest_v = valid_df.sort_values('amount', ascending=False).iloc[0]
                                        
                                        if valid_df['circ_mv'].min() < 999999999:
                                            smallest_m = valid_df[valid_df['circ_mv'] < 999999999].sort_values('circ_mv').iloc[0]
                                            mv_str = f"**{smallest_m['name']}** ({round(smallest_m['circ_mv']/10000, 2):.2f}亿)"
                                        else:
                                            smallest_m = valid_df.sort_values('amount').iloc[0]
                                            mv_str = f"**{smallest_m['name']}** (市值数据缺失，此为成交量最小替代)"
                                        
                                        df_strong = load_data("data/strong_stocks.csv")
                                        highest_lb_str = "暂无数据"
                                        if df_strong is not None and not df_strong.empty and 'name' in df_strong.columns:
                                            ind_strong = df_strong[df_strong['name'].isin(valid_df['name'])]
                                            if not ind_strong.empty and '连续天数' in ind_strong.columns:
                                                top_strong = ind_strong.sort_values('连续天数', ascending=False).iloc[0]
                                                # ★ 修复乌龙：将“连板”改回它真正的含义“强势常驻天数”
                                                highest_lb_str = f"{top_strong['name']} (强势常驻 {int(top_strong.get('连续天数',0))} 天，RPS:{top_strong.get('RPS_50',0):.1f})"
                                            else:
                                                highest_lb_str = "该板块今日暂无 RPS 强势标的"

                                        st.info(f"**👑 板块趋势中军 (上榜最久)**：\n\n **{highest_lb_str}**")
                                        st.success(f"**💡 极致性价比 (股价最低)**：\n\n **{lowest_p['name']}** ({lowest_p['close']:.2f}元)")
                                        st.warning(f"**🎈 绝佳炒作弹性 (流通市值最小)**：\n\n {mv_str}")
                                        st.error(f"**🌊 流动性中枢 (成交额最大)**：\n\n **{largest_v['name']}** ({round(largest_v['amount']/100000, 2):.2f}亿)")
                                    else:
                                        st.caption("该板块暂无有效交易数据。")
                                else:
                                    st.caption("未匹配到有效的行业属性。")
                            else:
                                st.caption("未能在行情大表中找到该股票。")
                        else:
                            st.warning("⚠️ 后台全市场快照数据正在生成中，请先去 GitHub 手动触发一次流水线（Daily Update）。")
                else:
                    st.caption("没有读取到有效的龙头数据。")
            else:
                st.caption("今日无涨停逻辑数据支撑发散。")
    else:
        st.warning("⏳ AI 正在推演今日战况，请稍后...")

    # === B. 核心 KPI ===
    if df_sent is not None and not df_sent.empty:
        last = df_sent.iloc[-1]
        c1, c2, c3 = st.columns(3)
        net_up = int(last['up_count'] - last['down_count'])
        c1.metric("上涨家数", f"{int(last['up_count'])}", delta=f"{net_up} (净值)")
        c2.metric("下跌家数", f"{int(last['down_count'])}", delta_color="inverse")
        c3.metric("全市场成交", f"{last['total_amount_yi']} 亿", help="低于 6000亿 需警惕流动性风险")
    
    # === C. 赚钱效应趋势 ===
    st.subheader("📊 市场情绪 (进攻热度监控)")
    if os.path.exists("data/market_sentiment.csv"):
        df_sent = pd.read_csv("data/market_sentiment.csv").sort_values('date')
        df_sent['up_ma5'] = df_sent['up_count'].rolling(window=5, min_periods=1).mean()
        df_show = df_sent.tail(60).copy()
        df_show['date_str'] = df_show['date'].astype(str)
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_show['date_str'], y=df_show['up_count'], name='上涨家数', line=dict(color='#ff4d4d', width=1.5), opacity=0.8, mode='lines'))
        fig.add_trace(go.Scatter(x=df_show['date_str'], y=df_show['up_ma5'], name='5日情绪线', line=dict(color='#FF9900', width=3), mode='lines'))
        fig.update_layout(title="多头情绪趋势 (MA5橙线为生命线)", xaxis=dict(type='category', tickangle=-45, tickmode='auto', nticks=15, gridcolor='rgba(128,128,128,0.2)'), yaxis=dict(title="上涨家数", gridcolor='rgba(128,128,128,0.2)'), legend=dict(orientation="h", y=1.05, x=0, xanchor="left"), margin=dict(l=0, r=0, t=40, b=0), hovermode="x unified", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("暂无历史情绪数据，请等待数据积累。")
    
    # === D. 大军团作战 ===
    st.subheader("🔥 大军团作战 (今日领涨板块)")
    if df_sector is not None and not df_sector.empty:
        fig_sec = px.bar(df_sector, x='industry', y='pct_chg', color='pct_chg', text='amount', labels={'pct_chg': '涨幅(%)', 'industry': '板块'}, color_continuous_scale='Reds', height=350)
        fig_sec.update_traces(texttemplate='%{text}亿', textposition='outside')
        st.plotly_chart(fig_sec, use_container_width=True)

    # === D2. 连板情绪天梯 (涨停高度与梯队) ===
    st.subheader("🪜 连板情绪天梯 (打板与连板接力)")
    ladder_raw = load_json("data/limit_ladder.json")
    
    if ladder_raw and "stocks" in ladder_raw and len(ladder_raw["stocks"]) > 0:
        total_zt = ladder_raw.get("total_count", len(ladder_raw["stocks"]))
        max_h = ladder_raw.get("max_height", 1)
        st.caption(f"👑 今日涨停共 **{total_zt}** 家 | 市场最高板：**{max_h} 连板** (统计日期: `{ladder_raw.get('date', '今日')}`)")
        
        df_zt_all = pd.DataFrame(ladder_raw["stocks"])
        df_zt_all['limit_times'] = pd.to_numeric(df_zt_all['limit_times'], errors='coerce').fillna(1).astype(int)
        
        # 梯队折叠展示
        for lb in sorted(df_zt_all['limit_times'].unique(), reverse=True):
            sub = df_zt_all[df_zt_all['limit_times'] == lb]
            badge = f"🔥 {lb} 连板" if lb > 1 else "🌱 首板"
            with st.expander(f"{badge} ({len(sub)} 家)", expanded=(lb >= 2)):
                cols = st.columns(min(len(sub), 4))
                for idx, (_, r) in enumerate(sub.iterrows()):
                    with cols[idx % 4]:
                        st.markdown(f"**{r['name']}** `{r.get('code', '')}`")
                        ind_str = r.get('industry', '-')
                        st.caption(f"行业: `{ind_str}`")
                        if r.get('reason'):
                            st.caption(f"💡 {r['reason']}")
    elif ai_data and 'limit_reasons' in ai_data and len(ai_data['limit_reasons']) > 0:
        # 降级展示 AI 抓到的涨停龙头
        st.caption("💡 当前为 AI 推演提取的重点龙头梯队：")
        df_reasons = pd.DataFrame(ai_data['limit_reasons'])
        for idx, r in df_reasons.iterrows():
            st.markdown(f"- **{r['name']}**: {r.get('reason', '')}")
    else:
        st.info("今日暂无涨停连板梯队数据。")

    # === E. 风格监控 ===
    st.subheader("⚖️ 风格监控 (500 vs 1000)")
    if df_index is not None:
        df_show = df_index.head(250).sort_values('date') 
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_show['date'], y=df_show['zz500'], name='中证500', line=dict(color='#ff9f43')))
        fig.add_trace(go.Scatter(x=df_show['date'], y=df_show['zz1000'], name='中证1000', line=dict(color='#00d2d3')))
        fig.add_trace(go.Scatter(x=df_show['date'], y=df_show['spread'], name='价差', line=dict(color='gray', dash='dot'), yaxis='y2'))
        fig.update_layout(yaxis2=dict(overlaying='y', side='right', showgrid=False), height=350, legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, use_container_width=True)

def render_stock_content(df):
    if df is None or df.empty: 
        st.info("暂无数据，请等待每日更新")
        return
    with st.expander("🔍 深度筛选 (点击展开/收起)", expanded=True):
        sc1, sc2, sc3, sc4 = st.columns([1, 1, 1, 1.5])
        min_d = sc1.slider("连榜天数", 1, 30, 1)
        min_rps = sc2.slider("最低 RPS", 50, 99, 87)
        max_pe = 1000
        if 'pe_ttm' in df.columns: max_pe = sc3.slider("最大 PE(TTM)", 0, 200, 100)
        opts = ["全部"]
        if '细分行业' in df.columns: opts += sorted([x for x in df['细分行业'].dropna().unique() if str(x) != 'nan'])
        ind = sc4.selectbox("题材/行业", opts)
        kw = st.text_input("搜索代码/名称", placeholder="输入代码或名称...")

    mask = (df['连续天数'] >= min_d) & (df['RPS_50'] >= min_rps)
    if 'pe_ttm' in df.columns: mask &= (df['pe_ttm'] <= max_pe) & (df['pe_ttm'] > 0)
    if ind != "全部": mask &= (df['细分行业'] == ind)
    if kw: mask &= (df['ts_code'].astype(str).str.contains(kw) | df['name'].str.contains(kw))
    
    show_df = df[mask].sort_values('RPS_50', ascending=False).copy()
    show_df = format_rps_show(show_df, 'RPS_50', 'rps_50_chg')
    cols = ['ts_code', 'name', '细分行业', 'price_now', 'pe_ttm', 'mv_亿', 'turnover_rate', 'RPS_50_Show', 'RPS_120', 'RPS_250', '连续天数', 'xueqiu_url']
    final_cols = [c for c in cols if c in show_df.columns]
    
    # 顶部统计 KPI
    col_k1, col_k2, col_k3 = st.columns(3)
    col_k1.metric("入选强势标的", f"{len(show_df)} 只")
    if 'mv_亿' in show_df.columns and not show_df.empty:
        col_k2.metric("中位数市值", f"{show_df['mv_亿'].median():.1f} 亿")
    if '连续天数' in show_df.columns and not show_df.empty:
        col_k3.metric("最长在榜", f"{show_df['连续天数'].max()} 天")

    st.dataframe(
        show_df[final_cols], 
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "RPS_50_Show": st.column_config.TextColumn("RPS 50 (强度)"),
            "细分行业": st.column_config.TextColumn("题材"),
            "price_now": st.column_config.NumberColumn("现价", format="%.2f"),
            "pe_ttm": st.column_config.NumberColumn("PE(TTM)", format="%.1f"),
            "mv_亿": st.column_config.NumberColumn("市值(亿)", format="%.1f"),
            "turnover_rate": st.column_config.NumberColumn("换手%", format="%.1f"),
        }, use_container_width=True, hide_index=True, height=800
    )

    # 快捷下载 CSV
    csv_bytes = show_df[final_cols].to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 导出筛选出的强势股 (CSV)",
        data=csv_bytes,
        file_name="strong_stocks_export.csv",
        mime="text/csv"
    )

def render_breakout_content(df):
    if df is None or df.empty: 
        st.info("💡 暂无阶段新高突破标的数据，将在每日收盘后计算生成。")
        return
    
    st.success(f"🚀 **Stage 2 突破动量捕捉器**：今日共捕获 **{len(df)}** 只突破关键阻力位标的")
    
    with st.expander("🔍 突破维度筛选", expanded=True):
        b1, b2, b3 = st.columns([1.5, 1.5, 1])
        level_opts = ["全部"] + sorted(df['level'].unique().tolist())
        sel_level = b1.selectbox("突破周期级别", level_opts)
        
        min_vol_ratio = b2.slider("放量倍数 (相比5日均量)", 1.0, 5.0, 1.2, step=0.1)
        kw_b = b3.text_input("搜代码/名称", placeholder="搜索...", key="kw_breakout")

    mask = df['vol_ratio'] >= min_vol_ratio
    if sel_level != "全部":
        mask &= (df['level'] == sel_level)
    if kw_b:
        mask &= (df['ts_code'].astype(str).str.contains(kw_b) | df['name'].str.contains(kw_b))

    show_df = df[mask].sort_values(by=['pct_chg', 'vol_ratio'], ascending=[False, False]).copy()
    
    # 统计卡片
    c1, c2, c3 = st.columns(3)
    c1.metric("筛选标的数", f"{len(show_df)} 只")
    if not show_df.empty:
        c2.metric("平均当日涨幅", f"+{show_df['pct_chg'].mean():.2f}%")
        c3.metric("最高放量倍数", f"{show_df['vol_ratio'].max():.1f} 倍")

    cols = ['ts_code', 'name', 'level', 'industry', 'close', 'pct_chg', 'vol_ratio', 'turnover_rate', 'mv_亿', 'pe_ttm', 'xueqiu_url']
    final_cols = [c for c in cols if c in show_df.columns]
    
    st.dataframe(
        show_df[final_cols], 
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "level": st.column_config.TextColumn("突破级别"),
            "name": st.column_config.TextColumn("名称"),
            "industry": st.column_config.TextColumn("题材行业"),
            "close": st.column_config.NumberColumn("现价", format="%.2f"),
            "pct_chg": st.column_config.NumberColumn("涨幅%", format="%.2f %%"),
            "vol_ratio": st.column_config.NumberColumn("放量(倍)", format="%.2f"),
            "turnover_rate": st.column_config.NumberColumn("换手%", format="%.2f"),
            "mv_亿": st.column_config.NumberColumn("市值(亿)", format="%.1f"),
            "pe_ttm": st.column_config.TextColumn("PE(TTM)")
        }, use_container_width=True, hide_index=True, height=700
    )

def render_etf_content(df):
    if df is None or df.empty: 
        st.info("暂无数据")
        return
    st.success(f"📈 捕捉到 {len(df)} 只强势 ETF")
    kw = st.text_input("🔍 搜 ETF")
    show_df = df.copy()
    if kw: show_df = show_df[show_df['name'].str.contains(kw) | show_df['ts_code'].str.contains(kw)]
    show_df = format_rps_show(show_df, 'RPS_50', 'rps_50_chg')
    target_cols = ['ts_code', 'name', 'price_now', 'RPS_50_Show', 'RPS_120', 'RPS_250', 'xueqiu_url']
    final_cols = [c for c in target_cols if c in show_df.columns]
    st.dataframe(
        show_df[final_cols], 
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "RPS_50_Show": st.column_config.TextColumn("RPS 50"),
            "price_now": st.column_config.NumberColumn("现价", format="%.3f"),
        }, use_container_width=True, hide_index=True, height=800
    )

def render_arbitrage_page():
    st.header("⚡ 投机与套利")
    st.caption("捕捉 A股可转债双低、跨境ETF溢价及市场错误定价机会")
    data = load_json("data/speculation_data.json")
    if not data:
        st.warning("📊 数据初始化中，请等待收盘更新...")
        return
    def make_xq_link(code):
        try:
            market = code.split('.')[-1].upper()
            num = code.split('.')[0]
            return f"https://xueqiu.com/S/{market}{num}"
        except: return ""

    st.subheader("🛡️ 可转债雷达")
    if data.get('cb_list'):
        cb_df = pd.DataFrame(data['cb_list'])
        cb_df['xueqiu_url'] = cb_df['ts_code'].apply(make_xq_link)
        if 'close' in cb_df.columns and 'premium_rate' in cb_df.columns:
            if 'double_low' not in cb_df.columns: cb_df['double_low'] = cb_df['close'] + cb_df['premium_rate']
            st.info(f"⚖️ **全市场可转债温度计**：当前平均双低值 **{cb_df['double_low'].mean():.2f}** | 平均溢价率 **{cb_df['premium_rate'].mean():.2f}%**")

        t1, t2 = st.tabs(["🛡️ 极致小盘 (潜伏)", "🔥 活跃妖债 (进攻)"])
        base_cols = ["ts_code", "bond_short_name", "close"]
        extra_cols = []
        if 'pct_chg' in cb_df.columns: extra_cols.append("pct_chg")       
        if 'double_low' in cb_df.columns: extra_cols.append("double_low") 
        if 'premium_rate' in cb_df.columns: extra_cols.append("premium_rate") 
        
        cb_col_config = {
            "ts_code": st.column_config.TextColumn("代码"),
            "bond_short_name": st.column_config.TextColumn("名称"),
            "close": st.column_config.NumberColumn("价格", format="%.3f"),
            "pct_chg": st.column_config.NumberColumn("涨幅%", format="%.2f %%"),
            "double_low": st.column_config.NumberColumn("双低值", format="%.2f"),
            "premium_rate": st.column_config.NumberColumn("溢价率%", format="%.2f %%"),
            "desc": st.column_config.TextColumn("规模/标签"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️")
        }

        with t1:
            mask_low = cb_df['tag'].str.contains('小盘') | cb_df['tag'].str.contains('双低')
            low_df = cb_df[mask_low].copy()
            if not low_df.empty:
                show_cols_t1 = [c for c in base_cols + extra_cols + ["desc", "xueqiu_url"] if c in low_df.columns]
                st.dataframe(low_df[show_cols_t1], column_config=cb_col_config, use_container_width=True, hide_index=True)
            else: st.caption("今日无符合【潜伏】策略的标的")
            
        with t2:
            mask_high = cb_df['tag'].str.contains('妖债') | cb_df['tag'].str.contains('龙头')
            high_df = cb_df[mask_high].copy()
            if not high_df.empty:
                show_cols_t2 = [c for c in base_cols + extra_cols + ["xueqiu_url"] if c in high_df.columns]
                st.dataframe(high_df[show_cols_t2], column_config=cb_col_config, use_container_width=True, hide_index=True)
            else: st.caption("今日无符合【进攻】策略的标的")
    else: st.info("今日无可转债数据。")

    st.markdown("---")
    st.subheader("🧱 跨境/LOF 溢价监控")
    if data.get('fund_list'):
        fund_df = pd.DataFrame(data['fund_list'])
        fund_df['xueqiu_url'] = fund_df['code'].apply(make_xq_link)
        fund_cols = ['code', 'name', 'price', 'change']
        if 'premium_rate' in fund_df.columns: fund_cols.append('premium_rate')
        elif 'est_premium' in fund_df.columns: fund_cols.append('est_premium')
        fund_cols.extend(['vol_yi', 'tag', 'xueqiu_url'])
        fund_cols = [c for c in fund_cols if c in fund_df.columns]
        
        st.dataframe(
            fund_df[fund_cols], 
            column_config={
                "code": st.column_config.TextColumn("代码"), "name": st.column_config.TextColumn("名称"),
                "price": st.column_config.NumberColumn("现价", format="%.3f"), "change": st.column_config.NumberColumn("涨幅%", format="%.2f %%"),
                "premium_rate": st.column_config.NumberColumn("溢价率%", format="%.2f %%"), "est_premium": st.column_config.NumberColumn("溢价率%", format="%.2f %%"),
                "vol_yi": st.column_config.NumberColumn("成交额(亿)", format="%.2f"), "tag": st.column_config.TextColumn("状态"),
                "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️")
            }, use_container_width=True, hide_index=True
        )

def render_guru_tracker():
    st.header("📚 投资作业本 (Guru Tracker)")
    st.caption("跟随全球顶尖大脑的资金流向，寻找下一个 Alpha。")
    st.info("💡 提示：机构持仓数据来源于 13F 报告，通常滞后季度结束 45 天；议员交易为实时披露。")

    if "selected_guru" not in st.session_state: st.session_state.selected_guru = None
    if st.session_state.selected_guru is None:
        st.subheader("🏛️ 基金经理名人堂 & 国会山股神")
        cols = st.columns(3)
        for index, (gid, info) in enumerate(GURUS.items()):
            col = cols[index % 3]
            with col:
                with st.container(border=True):
                    if os.path.exists(info['avatar']): image_stretch(info['avatar'])
                    else: st.warning("照片缺失")
                    st.subheader(info['name'])
                    st.caption(info['company'])
                    st.markdown(f"**风格**: {info['style']}")
                    if st.button(f"查看持仓 🔍", key=f"btn_{gid}"):
                        st.session_state.selected_guru = gid
                        st.rerun()
    else:
        gid = st.session_state.selected_guru
        info = GURUS[gid]
        if st.button("⬅️ 返回大厅"):
            st.session_state.selected_guru = None
            st.rerun()
        st.divider()
        col1, col2 = st.columns([1, 4])
        with col1:
            if os.path.exists(info['avatar']): image_stretch(info['avatar'])
        with col2:
            st.markdown(f"## {info['name']}")
            st.markdown(f"**{info['company']}** | *{info['style']}*")
            st.markdown(f"[🔗 查看原始数据源]({info.get('source_url', '')})")
            
        st.subheader("📝 最新持仓/交易明细")
        csv_path = f"data/gurus/{gid}_latest.csv"
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            st.dataframe(df, use_container_width=True, height=500)
        else:
            st.warning(f"🚧 暂未获取到 {info['name']} 的数据。请等待后台自动抓取脚本运行。")

def render_manual_page():
    st.header("📖 Chilam Club 驾驶舱使用指南")
    st.caption("“工欲善其事，必先利其器” —— 投资逻辑与指标释义")
    
    with st.expander("🔥 强势股 (RPS 动量策略) 玩法说明", expanded=True):
        st.markdown("""
        **1. 什么是 RPS (欧奈尔相对强度)？**
        RPS 衡量的是某只股票在过去一段时间内，涨幅超越全市场多少比例的股票。
        - **RPS_50 = 90**：代表该股过去 50 天的涨幅，超过了全市场 90% 的股票。
        - **核心玩法**：只买 RPS 大于 87 的股票（优等生里的尖子生），越靠近 100 动量越强。结合“趋势常驻天数”和“红绿箭头”，寻找刚启动的强势股或龙头接力。
        """)

    with st.expander("🛡️ 可转债双低 (潜伏) 与 妖债 (进攻)", expanded=False):
        st.markdown("""
        **1. 什么是双低值？**
        `双低值 = 可转债价格 + 转股溢价率`。
        - 双低策略是经典的“下有保底，上不封顶”玩法。
        - 双低值越小，代表价格越便宜且转股溢价越低。低于市场平均双低值（通常在120-130左右）的转债，安全垫极高。
        """)

    with st.expander("🧱 跨境/LOF 溢价套利机制", expanded=False):
        st.markdown("""
        **1. 什么是溢价率？**
        溢价率 = `(场内交易价格 - 基金实际净值) / 基金实际净值`
        - 懂申赎机制的资金会在场外（按净值）申购，转到场内（按高价）抛售，俗称“搬砖”。这会导致高溢价最终必然回落（被砸盘）。监控溢价率，主要是为了防范追高被埋。
        """)

def render_vip_lock(feature_name: str):
    """VIP 页面锁定卡片"""
    st.warning(f"🔒 **{feature_name}** 为 VIP 会员专享模块")
    if not auth.is_logged_in():
        st.info("💡 请先登录账号。若未注册，可点击下方按钮注册/登录。")
        if st.button("👉 登录 / 注册", key=f"btn_login_{feature_name}"):
            st.switch_page("pages/auth.py")
    else:
        st.info(f"当前登录账号：`{auth.get_user_email()}` (未开通 VIP)")
        st.markdown("欢迎前往会员中心查看 VIP 订阅特权与开通指引。")
        if st.button("👉 前往会员中心开通", key=f"btn_vip_{feature_name}"):
            st.switch_page("pages/dashboard.py")

def main():
    with st.sidebar:
        st.title("Chilam.Club")
        
        # 登录状态展示
        if auth.is_logged_in():
            u_email = auth.get_user_email()
            if auth.is_admin():
                st.caption(f"👤 `{u_email}` (🛡️管理员)")
            elif auth.is_vip():
                st.caption(f"👤 `{u_email}` (👑VIP)")
            else:
                st.caption(f"👤 `{u_email}` (普通用户)")
            
            c_m1, c_m2 = st.columns(2)
            with c_m1:
                if st.button("会员中心", key="sb_dash", use_container_width=True):
                    st.switch_page("pages/dashboard.py")
            with c_m2:
                if st.button("退出", key="sb_logout", use_container_width=True):
                    auth.logout()
                    st.rerun()
            if auth.is_admin():
                if st.button("⚙️ 后台管理", key="sb_admin", use_container_width=True):
                    st.switch_page("admin.py")
        else:
            st.caption("👤 游客未登录")
            if st.button("🔐 登录 / 注册", key="sb_login", use_container_width=True):
                st.switch_page("pages/auth.py")

        st.markdown("---")
        menu_items = [
            "🛸 全市场看板",
            "⚡ 实时行情 (分时/K线)",
            "🔥 强势股",
            "⭐ 自选股雷达",
            "🌐 宏观与股债性价比",
            "⚡ 投机与套利",
            "🚨 核心龙头雷达",  
            "📚 投资作业本",
            "📏 黄金分割预测",
            "📖 使用说明文档"
        ]
        page = st.radio("导航菜单", menu_items, index=0)
        st.markdown("---")
        st.markdown("💡 **Tip**: 保持独立思考。")
        if os.path.exists("donate.jpg"):
            st.image("donate.jpg", caption="请喝咖啡 ☕")

    if page == "🛸 全市场看板":
        render_market_dashboard()
    elif page == "⚡ 实时行情 (分时/K线)":
        render_live_quote_page()
    elif page == "🔥 强势股":
        if not auth.is_vip():
            render_vip_lock("🔥 强势股 (RPS 动量策略)")
        else:
            df_stock = load_data("data/strong_stocks.csv")
            df_breakout = load_data("data/breakout_stocks.csv")
            df_etf = load_data("data/strong_etfs.csv")
            t1, t2, t3 = st.tabs(["🐉 RPS 个股", "🚀 阶段新高突破", "💰 强势 ETF"])
            with t1: render_stock_content(df_stock)
            with t2: render_breakout_content(df_breakout)
            with t3: render_etf_content(df_etf)
    elif page == "⭐ 自选股雷达":
        if not auth.is_logged_in():
            render_vip_lock("⭐ 个人自选股雷达 (云端持久化)")
        else:
            render_watchlist_page()
    elif page == "🌐 宏观与股债性价比":
        render_macro_erp_page()
    elif page == "⚡ 投机与套利":
        if not auth.is_vip():
            render_vip_lock("⚡ 投机与套利 (可转债双低/溢价套利)")
        else:
            render_arbitrage_page()
    elif page == "🚨 核心龙头雷达":          
        render_core_driver_page()
    elif page == "📚 投资作业本":
        if not auth.is_vip():
            render_vip_lock("📚 投资作业本 (顶尖机构持仓追踪)")
        else:
            render_guru_tracker()
    elif page == "📏 黄金分割预测":
        render_fibonacci_chart()
    elif page == "📖 使用说明文档":
        render_manual_page()

if __name__ == "__main__":
    main()

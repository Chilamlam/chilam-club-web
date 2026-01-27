import streamlit as st
import pandas as pd
import akshare as st_ak
import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# 1. 基础配置
st.set_page_config(page_title="Chilam Club - 投资驾驶舱", page_icon="🚀", layout="wide")

# 2. 辅助函数
def load_data(path):
    if not os.path.exists(path): return None
    try:
        return pd.read_csv(path)
    except: return None

# ★ 新增：RPS 美化逻辑 (用于生成带箭头的字符串)
def format_rps_show(df, rps_col='RPS_50', chg_col='rps_50_chg'):
    """
    输入：DataFrame
    输出：增加了 RPS_50_Show 列的 DataFrame
    """
    if df is None or df.empty: return df
    
    # 如果后端脚本还没生成 change 列，就只显示数值
    if chg_col not in df.columns:
        df[f'{rps_col}_Show'] = df[rps_col].map(lambda x: f"{x:.1f}")
        return df

    def _fmt(row):
        val = row[rps_col]
        chg = row[chg_col]
        
        # 999 是后端定义的 "New" 标记
        if chg == 999:
            return f"{val:.1f} 🆕"
        elif chg > 0:
            return f"{val:.1f} 🔺{abs(chg):.1f}"
        elif chg < 0:
            return f"{val:.1f} 🔻{abs(chg):.1f}"
        else:
            return f"{val:.1f} -"
            
    df[f'{rps_col}_Show'] = df.apply(_fmt, axis=1)
    return df

# ================= 新闻模块 (保持原样) =================
@st.cache_data(ttl=300)
def get_news_data():
    try:
        return st_ak.stock_info_global_cls()
    except Exception:
        return pd.DataFrame({
            "标题": ["接口繁忙"], 
            "发布日期": ["-"], 
            "发布时间": ["-"], 
            "内容": ["无法获取数据，请稍后重试..."]
        })

def render_news_page():
    st.header("📰 实时新闻挖掘【免费服务每五分钟更新】")
    st.caption("Powered by 全天候攻略")
    
    if "ZHIPU_API_KEY" in st.secrets:
        api_key = st.secrets["ZHIPU_API_KEY"]
    else:
        # 兼容本地环境变量读取
        api_key = os.getenv("ZHIPU_API_KEY", "")
        if not api_key:
            st.error("请在 Streamlit 后台或环境变量配置 ZHIPU_API_KEY")
            return

    with st.spinner('正在连接全球财经资讯...'):
        news_df = get_news_data()

    if 'selected_idx' not in st.session_state:
        st.session_state.selected_idx = 0

    col_list, col_detail = st.columns([3, 7])

    with col_list:
        st.subheader("实时流")
        for idx, row in news_df.head(30).iterrows():
            with st.container():
                status = "primary" if idx == st.session_state.selected_idx else "secondary"
                title_text = str(row['标题'])
                btn_label = f"📄 {title_text[:18]}..."
                if st.button(btn_label, key=f"news_{idx}", type=status, use_container_width=True):
                    st.session_state.selected_idx = idx
                    st.rerun()

    with col_detail:
        if not news_df.empty:
            current = news_df.iloc[st.session_state.selected_idx]
            
            st.markdown("---")
            st.subheader(current['标题'])
            st.caption(f"发布时间: {current['发布日期']} {current['发布时间']}")
            st.info(current['内容'])

            st.markdown("### 🧠 AI分析")
            if st.button("✨ 挖掘概念与龙头", type="primary"):
                with st.spinner("AI 正在分析核心逻辑..."):
                    try:
                        llm = ChatOpenAI(
                            api_key=api_key,
                            base_url="https://open.bigmodel.cn/api/paas/v4/",
                            model="glm-4-flash",
                            temperature=0.3
                        )
                        prompt = ChatPromptTemplate.from_messages([
                            ("system", "你是一位专业的财经证券分析师。请阅读用户提供的财经新闻，完成以下任务：判断利好或者利空，提取核心概念，并挖掘相关A股龙头。请用Markdown输出。"),
                            ("user", "标题：{title}\n内容：{content}")
                        ])
                        chain = prompt | llm | StrOutputParser()
                        res = chain.invoke({"title": current['标题'], "content": current['内容']})
                        st.success("分析完成")
                        st.markdown(res)
                    except Exception as e:
                        st.error(f"AI 分析服务暂时不可用: {e}")

# ================= 强势股 & ETF 页面 (升级版) =================

def render_stock_content(df):
    """封装个股显示逻辑 (含细分行业 + RPS变化对比 + 雪球跳转)"""
    if df is None or df.empty:
        st.info("📊 股票数据初始化中...")
        return
        
    c1, c2, c3 = st.columns(3)
    c1.metric("入选数量", f"{len(df)} 只")
    
    if 'pe_ttm' in df.columns:
        value_count = len(df[(df['pe_ttm'] > 0) & (df['pe_ttm'] < 30)])
        c2.metric("低估值(PE<30)", f"{value_count} 只")
    else:
        c2.metric("妖股(>10天)", f"{len(df[df['连续天数']>=10])} 只")
        
    date_label = df['更新日期'].iloc[0] if '更新日期' in df.columns else "未知"
    c3.markdown(f"**日期**: {date_label}")
    
    with st.expander("🔍 深度筛选", expanded=True):
        sc1, sc2, sc3 = st.columns([1, 1.2, 1])
        # 1. 连板
        min_d = sc1.slider("至少连续上榜(天)", 1, 30, 1)
        
        # 2. ★ 新增：细分行业筛选
        selected_industry = "全部"
        if '细分行业' in df.columns:
            # 过滤空值并排序
            industries = ["全部"] + sorted([x for x in df['细分行业'].dropna().unique() if x != '-'])
            selected_industry = sc2.selectbox("按细分题材筛选", industries)
            
        # 3. 搜索
        kw = sc3.text_input("搜索代码/名称/行业")
        
    # 筛选逻辑
    mask = df['连续天数'] >= min_d
    
    if selected_industry != "全部":
        mask = mask & (df['细分行业'] == selected_industry)

    if kw: 
        # 同时匹配代码、名称、行业
        search_mask = (
            df['ts_code'].astype(str).str.contains(kw, case=False) | 
            df['name'].str.contains(kw, case=False)
        )
        if '细分行业' in df.columns:
            search_mask = search_mask | df['细分行业'].str.contains(kw, case=False)
        mask = mask & search_mask
    
    show_df = df[mask].sort_values('RPS_50', ascending=False).copy()
    
    # ★ 核心修改：生成 RPS 箭头列
    show_df = format_rps_show(show_df, 'RPS_50', 'rps_50_chg')

    # === 列配置 ===
    col_cfg = {
        "ts_code": st.column_config.TextColumn("代码", width="small"),
        "name": st.column_config.TextColumn("名称", width="small"),
        # ★ 新增：细分行业
        "细分行业": st.column_config.TextColumn("细分题材", width="medium", help="来源：东方财富细分行业"),
        "price_now": st.column_config.NumberColumn("现价", format="%.2f"),
        "pe_ttm": st.column_config.NumberColumn("PE", format="%.0f"),
        "mv_亿": st.column_config.NumberColumn("市值", format="%.0f亿"),
        "turnover_rate": st.column_config.NumberColumn("换手", format="%.1f%%"),
        
        # ★ 修改：显示 RPS 箭头
        "RPS_50_Show": st.column_config.TextColumn("RPS 50 (变化)", help="相对于昨日排名的变化"),
        
        "RPS_120": st.column_config.NumberColumn("RPS 120", format="%.1f"),
        "RPS_250": st.column_config.NumberColumn("RPS 250", format="%.1f"),
        "连续天数": st.column_config.NumberColumn("天数", format="%d"),
        
        # ★ 修改：雪球链接
        "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
    }
    
    # 动态构建显示列
    base_cols = ['ts_code', 'name', '细分行业', 'price_now', 'RPS_50_Show']
    extra_cols = ['mv_亿', 'pe_ttm', 'turnover_rate', 'RPS_120', 'RPS_250', '连续天数', 'xueqiu_url']
    
    final_cols = [c for c in base_cols + extra_cols if c in show_df.columns]

    st.dataframe(
        show_df[final_cols],
        column_config=col_cfg,
        use_container_width=True,
        hide_index=True,
        height=800
    )

def render_strong_page():
    st.header("🔥 市场强势信号池 (RPS)")
    st.caption("数据源：Tushare Pro | 每日 18:00 更新")

    df_stock = load_data("data/strong_stocks.csv")
    df_etf = load_data("data/strong_etfs.csv")

    tab1, tab2 = st.tabs(["🐉 个股龙虎榜", "💰 热门 ETF (Top100)"])
    
    with tab1:
        render_stock_content(df_stock)
        
    with tab2:
        if df_etf is not None and not df_etf.empty:
            st.success("📈 捕捉到强势 ETF 信号")
            kw_etf = st.text_input("🔍 搜 ETF (如: 半导体, 纳指)", "")
            show_etf = df_etf.copy()
            if kw_etf:
                show_etf = show_etf[show_etf['name'].str.contains(kw_etf) | show_etf['ts_code'].str.contains(kw_etf)]
            
            # ★ 核心修改：生成 RPS 箭头列
            show_etf = format_rps_show(show_etf, 'RPS_50', 'rps_50_chg')
            
            st.dataframe(
                show_etf.sort_values('RPS_50', ascending=False),
                column_config={
                    "ts_code": st.column_config.TextColumn("代码"),
                    "name": st.column_config.TextColumn("名称"),
                    "price_now": st.column_config.NumberColumn("现价", format="¥ %.3f"),
                    # ★ 修改：显示箭头
                    "RPS_50_Show": st.column_config.TextColumn("RPS 50 (变化)"),
                    "RPS_120": st.column_config.NumberColumn("RPS 120", format="%.1f"),
                    # ★ 修改：雪球链接
                    "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
                },
                use_container_width=True,
                hide_index=True,
                height=800
            )
        else:
            st.info("暂无 ETF 数据")

# ================= 主程序导航 =================
def main():
    with st.sidebar:
        st.title("Chilam.Club")
        st.markdown("公众号全天候攻略提供服务")
        
        page = st.radio(
            "功能导航", 
            ["📰 实时新闻挖掘", "🔥 市场强势股 (VIP)"],
            index=1
        )
        st.markdown("---")
        st.caption("数据支持：Akshare & Tushare")
        
        # 打赏区域
        st.markdown("---")
        st.markdown("#### ☕ 支持开发者")
        donate_img = "donate.jpg" 
        if os.path.exists(donate_img):
            st.image(
                donate_img, 
                caption="扫码请喝杯咖啡 ☕", 
                use_container_width=True
            )
        else:
            # 兼容原版逻辑，无图则提示，或者你可以删掉这行
            pass

    if page == "📰 实时新闻挖掘":
        render_news_page()
    elif page == "🔥 市场强势股 (VIP)":
        render_strong_page()

if __name__ == "__main__":
    main()

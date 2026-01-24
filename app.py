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
    return pd.read_csv(path)

# ================= 新闻模块 (保持不变) =================
@st.cache_data(ttl=300)
def get_news_data():
    try:
        return st_ak.stock_info_global_cls()
    except Exception:
        return pd.DataFrame({"标题": ["接口繁忙"], "发布日期": ["-"], "发布时间": ["-"], "内容": ["无法获取数据"]})

def render_news_page():
    st.header("📰 实时新闻【免费服务每五分钟更新一次】")
    st.caption("Powered by 全天候攻略")
    
    if "ZHIPU_API_KEY" in st.secrets:
        api_key = st.secrets["ZHIPU_API_KEY"]
    else:
        st.error("请配置 ZHIPU_API_KEY")
        return

    with st.spinner('连接全球资讯...'):
        news_df = get_news_data()

    if 'selected_idx' not in st.session_state:
        st.session_state.selected_idx = 0

    col_list, col_detail = st.columns([3, 7])
    with col_list:
        st.subheader("实时流")
        for idx, row in news_df.head(30).iterrows():
            with st.container():
                status = "primary" if idx == st.session_state.selected_idx else "secondary"
                if st.button(f"📄 {str(row['标题'])[:18]}...", key=f"news_{idx}", type=status, use_container_width=True):
                    st.session_state.selected_idx = idx
                    st.rerun()
    with col_detail:
        if not news_df.empty:
            current = news_df.iloc[st.session_state.selected_idx]
            st.markdown("---")
            st.subheader(current['标题'])
            st.info(current['内容'])
            if st.button("✨ AI 分析龙头", type="primary"):
                with st.spinner("AI 正在分析..."):
                    try:
                        llm = ChatOpenAI(api_key=api_key, base_url="https://open.bigmodel.cn/api/paas/v4/", model="glm-4-flash", temperature=0.3)
                        prompt = ChatPromptTemplate.from_messages([                        ("system", "你是一位专业的财经证券分析师。请阅读用户提供的财经新闻，完成以下任务：\n" 
                                                                                                    "0. **情绪识别**：分析该新闻是利好还是利空。\n"
                                                                                                    "1. **概念识别**：分析该新闻涉及的核心产业链概念（例如：Robotaxi, CPO, 创新药等）。\n"                                   
                                                                                                    "2. **个股挖掘**：根据概念，列出3-5只A股或港股中最相关的龙头个股名称，并用一句话解释关联理由。\n\n"                                  
                                                                                                    "输出格式请使用 Markdown，清晰分级。"),                        
                                                                                           ("user", "新闻标题：{title}\n\n新闻内容：{content}\n\n请开始分析。")                    ])
                        chain = prompt | llm | StrOutputParser()
                        res = chain.invoke({"title": current['标题'], "content": current['内容']})
                        st.markdown(res)
                    except Exception as e:
                        st.error(f"Error: {e}")

# ================= 3. 强势股 & ETF 页面 (升级版) =================
def render_strong_page():
    st.header("🔥 市场强势信号池 (RPS)")
    st.caption("数据源：Tushare Pro | 每日 17:00 更新")

    # 安全读取
    df_stock = load_data("data/strong_stocks.csv")
    df_etf = load_data("data/strong_etfs.csv")

    # 如果有 ETF 数据，显示 Tabs；否则回退到只显示个股
    if df_etf is not None and not df_etf.empty:
        tab1, tab2 = st.tabs(["🐉 个股龙虎榜", "💰 热门 ETF (Top100)"])
        
        with tab1:
            render_stock_content(df_stock)
            
        with tab2:
            st.success("📈 捕捉到成交额最大的 100 只非货币 ETF")
            # ETF 筛选
            kw_etf = st.text_input("🔍 搜 ETF (如: 半导体, 纳指)", "")
            show_etf = df_etf.copy()
            if kw_etf:
                show_etf = show_etf[show_etf['name'].str.contains(kw_etf) | show_etf['fund_type'].str.contains(kw_etf)]
            
            # ETF 表格
            st.dataframe(
                show_etf.sort_values('amount_亿', ascending=False),
                column_config={
                    "ts_code": st.column_config.TextColumn("代码"),
                    "amount_亿": st.column_config.NumberColumn("成交额", format="%.2f 亿"),
                    "price_now": st.column_config.NumberColumn("现价", format="¥ %.3f"),
                    "RPS_50": st.column_config.ProgressColumn("RPS 50", min_value=0, max_value=100, format="%.1f"),
                    "RPS_120": st.column_config.NumberColumn("RPS 120", format="%.1f"),
                    "eastmoney_url": st.column_config.LinkColumn("详情", display_text="K线➡️"),
                },
                use_container_width=True,
                hide_index=True,
                height=800
            )
    else:
        # 回退模式
        render_stock_content(df_stock)

def render_stock_content(df):
    """封装个股显示逻辑"""
    if df is None or df.empty:
        st.info("📊 股票数据初始化中...")
        return
        
    c1, c2, c3 = st.columns(3)
    c1.metric("入选数量", f"{len(df)} 只")
    c2.metric("妖股(>10天)", f"{len(df[df['连续天数']>=10])} 只")
    c3.markdown(f"**日期**: {df['更新日期'].iloc[0]}")
    
    with st.expander("🔍 个股筛选", expanded=True):
        sc1, sc2 = st.columns([1,2])
        min_d = sc1.slider("至少连续上榜", 1, 30, 1)
        kw = sc2.text_input("搜索股票")
        
    mask = df['连续天数'] >= min_d
    if kw: mask = mask & (df['ts_code'].astype(str).str.contains(kw) | df['name'].str.contains(kw))
    
    st.dataframe(
        df[mask].sort_values('RPS_50', ascending=False)[['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'RPS_120', '连续天数', 'eastmoney_url']],
        column_config={
            "eastmoney_url": st.column_config.LinkColumn("详情", display_text="K线➡️"),
            "RPS_50": st.column_config.ProgressColumn("RPS 50", min_value=80, max_value=100, format="%.1f"),
            "连续天数": st.column_config.NumberColumn("在榜", format="%d天"),
        },
        use_container_width=True,
        hide_index=True,
        height=800
    )

def main():
    with st.sidebar:
        st.title("Chilam.Club")
        st.markdown("“不得贪胜，步步登高”")
        page = st.radio("功能导航", ["📰 实时新闻挖掘", "🔥 市场强势股 (VIP)"], index=1)
        
    if page == "📰 实时新闻挖掘":
        render_news_page()
    elif page == "🔥 市场强势股 (VIP)":
        render_strong_page()

if __name__ == "__main__":
    main()

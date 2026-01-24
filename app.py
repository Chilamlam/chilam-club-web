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

# ================= 新闻模块 =================
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
        st.error("请在 Streamlit 后台配置 ZHIPU_API_KEY")
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

# ================= 强势股 & ETF 页面 =================
def render_strong_page():
    st.header("🔥 市场强势信号池 (RPS)")
    st.caption("数据源：Tushare Pro | 每日 17:00 更新")

    df_stock = load_data("data/strong_stocks.csv")
    df_etf = load_data("data/strong_etfs.csv")

    if df_etf is not None and not df_etf.empty:
        tab1, tab2 = st.tabs(["🐉 个股龙虎榜", "💰 热门 ETF (Top100)"])
        with tab1:
            render_stock_content(df_stock)
        with tab2:
            st.success("📈 捕捉到成交额最大的 100 只非货币 ETF")
            kw_etf = st.text_input("🔍 搜 ETF (如: 半导体, 纳指)", "")
            show_etf = df_etf.copy()
            if kw_etf:
                show_etf = show_etf[show_etf['name'].str.contains(kw_etf) | show_etf['fund_type'].str.contains(kw_etf)]
            
            st.dataframe(
                show_etf.sort_values('amount_亿', ascending=False),
                column_config={
                    "ts_code": st.column_config.TextColumn("代码"),
                    "amount_亿": st.column_config.NumberColumn("成交额", format="%.2f 亿"),
                    "price_now": st.column_config.NumberColumn("现价", format="¥ %.3f"),
                    "RPS_50": st.column_config.ProgressColumn("RPS 50", min_value=0, max_value=100, format="%.1f"),
                    "eastmoney_url": st.column_config.LinkColumn("详情", display_text="K线➡️"),
                },
                use_container_width=True,
                hide_index=True,
                height=800
            )
    else:
        render_stock_content(df_stock)

def render_stock_content(df):
    if df is None or df.empty:
        st.info("📊 股票数据初始化中，请等待自动更新...")
        return
        
    c1, c2, c3 = st.columns(3)
    c1.metric("入选数量", f"{len(df)} 只")
    c2.metric("妖股(>10天)", f"{len(df[df['连续天数']>=10])} 只")
    date_label = df['更新日期'].iloc[0] if '更新日期' in df.columns else "未知"
    c3.markdown(f"**日期**: {date_label}")
    
    with st.expander("🔍 个股筛选", expanded=True):
        sc1, sc2 = st.columns([1,2])
        min_d = sc1.slider("至少连续上榜", 1, 30, 1)
        kw = sc2.text_input("搜索股票代码/名称/行业")
        
    mask = df['连续天数'] >= min_d
    if kw: 
        mask = mask & (df['ts_code'].astype(str).str.contains(kw) | df['name'].str.contains(kw) | df['industry'].str.contains(kw))
    
    st.dataframe(
        df[mask].sort_values('RPS_50', ascending=False)[['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'RPS_120', '连续天数', 'eastmoney_url']],
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "eastmoney_url": st.column_config.LinkColumn("详情", display_text="K线➡️"),
            "price_now": st.column_config.NumberColumn("现价", format="¥ %.2f"),
            "RPS_50": st.column_config.ProgressColumn("RPS 50", min_value=80, max_value=100, format="%.1f"),
            "连续天数": st.column_config.NumberColumn("在榜", format="%d天"),
        },
        use_container_width=True,
        hide_index=True,
        height=800
    )

# ================= 4. 主程序导航 (新增打赏功能) =================
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
        
        # === 👇 新增打赏区域 👇 ===
        # 这里会留出一段空白，把二维码挤到底部
        st.markdown("---")
        st.markdown("#### ☕ 支持开发者")
        
        # 你的图片文件名，支持 jpg, png
        # 请确保你已经把 'donate.png' 上传到了 GitHub 根目录！
        donate_img = "donate.jpg" 
        
        if os.path.exists(donate_img):
            st.image(
                donate_img, 
                caption="扫码请喝杯咖啡 ☕", 
                use_container_width=True
            )
        else:
            st.info("（在此处展示打赏二维码，请上传 donate.jpg 到仓库）")

    if page == "📰 实时新闻挖掘":
        render_news_page()
    elif page == "🔥 市场强势股 (VIP)":
        render_strong_page()

if __name__ == "__main__":
    main()

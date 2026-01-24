import streamlit as st
import pandas as pd
import akshare as st_ak
import os
# 引入 AI 相关库
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# ================= 1. 页面配置 =================
st.set_page_config(
    page_title="Chilam Club - 投资驾驶舱",
    page_icon="🚀",
    layout="wide"
)

# ================= 2. 功能模块：实时新闻挖掘 =================
@st.cache_data(ttl=300)
def get_news_data():
    try:
        # 获取财联社电报
        return st_ak.stock_info_global_cls()
    except Exception:
        # 兜底数据，防止接口报错导致页面崩溃
        return pd.DataFrame({
            "标题": ["示例新闻：接口暂时繁忙", "示例新闻：请稍后刷新"],
            "发布日期": ["2026-01-24"] * 2,
            "发布时间": ["10:00:00", "11:00:00"],
            "内容": ["无法获取实时数据，请检查 Akshare 接口状态...", "waiting for recovery..."]
        })

def render_news_page():
    st.header("📰 实时新闻挖掘【免费服务每5分钟更新一次】")
    st.caption("Powered by 全天候攻略")
    
    # 获取 API Key (请确保你在 Streamlit Cloud 的 Secrets 里配置了 ZHIPU_API_KEY)
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
        # 仅显示前 30 条，避免页面卡顿
        for idx, row in news_df.head(30).iterrows():
            with st.container():
                status = "primary" if idx == st.session_state.selected_idx else "secondary"
                # 按钮显示新闻标题前 18 个字
                if st.button(f"📄 {str(row['标题'])[:18]}...", key=f"news_{idx}", type=status, use_container_width=True):
                    st.session_state.selected_idx = idx
                    st.rerun()

    with col_detail:
        if not news_df.empty:
            # 获取当前选中的新闻
            current = news_df.iloc[st.session_state.selected_idx]
            
            st.markdown("---")
            st.subheader(current['标题'])
            st.caption(f"时间：{current['发布日期']} {current['发布时间']}")
            st.info(current['内容'])

            st.markdown("### 🧠 简单分析")
            if st.button("✨ 挖掘概念与龙头", type="primary"):
                with st.spinner("AI 正在分析核心逻辑..."):
                    try:
                        # 调用大模型
                        llm = ChatOpenAI(
                            api_key=api_key,
                            base_url="https://open.bigmodel.cn/api/paas/v4/",
                            model="glm-4-flash",
                            temperature=0.3
                        )
                        prompt = ChatPromptTemplate.from_messages([
                            ("system", "你是专业分析师。请提取新闻中的核心产业链概念，并挖掘3只最相关的A股龙头股。请用Markdown格式输出，包含：【情绪判断：到底是利好还是利空或者是中性】【核心逻辑】、【受益板块】、【相关个股】。"),
                            ("user", "标题：{title}\n内容：{content}")
                        ])
                        chain = prompt | llm | StrOutputParser()
                        res = chain.invoke({"title": current['标题'], "content": current['内容']})
                        
                        st.success("分析完成")
                        st.markdown(res)
                    except Exception as e:
                        st.error(f"分析出错: {e}")

# ================= 3. 功能模块：市场强势股 (RPS) =================
def load_data(path):
    if not os.path.exists(path): return None
    return pd.read_csv(path)

def render_strong_page():
    st.header("🔥 市场强势信号池 (RPS)")
    st.caption("策略： RPS 三线红 (>87) | 数据源：Tushare Pro | 更新时间：每日 17:00")

    # 读取数据
    df = load_data("data/strong_stocks.csv")
    
    if df is None or df.empty:
        st.info("📊 数据尚未初始化，请等待今日收盘后首次更新。")
        return

    # 1. 顶部指标卡
    update_date = df['更新日期'].iloc[0] if '更新日期' in df.columns else "未知"
    c1, c2, c3 = st.columns(3)
    c1.metric("数据日期", update_date)
    c2.metric("入选数量", f"{len(df)} 只")
    # 统计连续上榜超过10天的妖股
    c3.metric("妖股预备(>10天)", f"{len(df[df['连续天数']>=10])} 只")
    
    st.markdown("---")

    # 2. 筛选工具
    with st.expander("🔍 筛选工具", expanded=True):
        sc1, sc2 = st.columns([1,2])
        min_d = sc1.slider("至少连续上榜天数", 1, 30, 1)
        kw = sc2.text_input("搜索股票代码/名称")
    
    # 3. 数据过滤
    mask = df['连续天数'] >= min_d
    if kw: 
        mask = mask & (df['ts_code'].astype(str).str.contains(kw) | df['name'].str.contains(kw))
    
    show_df = df[mask].sort_values('RPS_50', ascending=False)

    # 4. 展示表格 (配置了跳转链接)
    st.dataframe(
        show_df[['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'RPS_120', 'RPS_250', '连续天数', 'eastmoney_url']],
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "eastmoney_url": st.column_config.LinkColumn(
                "详情", 
                display_text="K线➡️", 
                help="点击跳转东方财富行情"
            ),
            "price_now": st.column_config.NumberColumn("现价", format="¥ %.2f"),
            "RPS_50": st.column_config.ProgressColumn("RPS 50 (短期)", min_value=80, max_value=100, format="%.1f"),
            "RPS_120": st.column_config.NumberColumn("RPS 120 (中期)", format="%.1f"),
            "RPS_250": st.column_config.NumberColumn("RPS 250 (长期)", format="%.1f"),
            "连续天数": st.column_config.NumberColumn("连续在榜", format="%d 天"),
        },
        use_container_width=True,
        hide_index=True,
        height=800
    )

# ================= 4. 主程序导航 =================
def main():
    with st.sidebar:
        st.title("Chilam.Club")
        st.markdown("公众号全天候攻略提供")
        
        # 侧边栏导航
        page = st.radio(
            "功能导航", 
            ["📰 实时新闻挖掘", "🔥 市场强势股 (VIP)"],
            index=1  # 默认显示强势股页面
        )
        st.markdown("---")
        st.caption("数据支持：Akshare & Tushare")

    if page == "📰 实时新闻挖掘":
        render_news_page()
    elif page == "🔥 市场强势股 (VIP)":
        render_strong_page()

if __name__ == "__main__":
    main()


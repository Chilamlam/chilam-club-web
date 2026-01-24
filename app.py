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

# ================= 2. 功能函数：新闻挖掘 =================
@st.cache_data(ttl=300)
def get_news_data():
    try:
        return st_ak.stock_info_global_cls()
    except Exception:
        # 模拟数据防止报错
        return pd.DataFrame({
            "标题": ["示例新闻：市场情绪回暖", "示例新闻：科技股领涨"],
            "发布日期": ["2026-01-24"] * 2,
            "发布时间": ["10:00:00", "11:00:00"],
            "内容": ["这里是模拟内容...", "这里是模拟内容..."]
        })

def render_news_page():
    st.header("📰 实时新闻挖掘")
    st.caption("Powered by Chilam Club & GLM-4")
    
    # 获取 API Key
    if "ZHIPU_API_KEY" in st.secrets:
        api_key = st.secrets["ZHIPU_API_KEY"]
    else:
        st.error("请在 Streamlit后台配置 ZHIPU_API_KEY")
        return

    with st.spinner('正在连接全球财经资讯...'):
        news_df = get_news_data()

    if 'selected_idx' not in st.session_state:
        st.session_state.selected_idx = 0

    col_list, col_detail = st.columns([3, 7])

    with col_list:
        st.subheader("实时流")
        for idx, row in news_df.iterrows():
            if idx > 20: break # 只显示前20条防止太卡
            with st.container():
                status = "primary" if idx == st.session_state.selected_idx else "secondary"
                if st.button(f"📄 {row['标题'][:15]}...", key=f"news_{idx}", type=status, use_container_width=True):
                    st.session_state.selected_idx = idx
                    st.rerun()

    with col_detail:
        if not news_df.empty:
            current = news_df.iloc[st.session_state.selected_idx]
            st.markdown("---")
            st.subheader(current['标题'])
            st.caption(f"时间：{current['发布日期']} {current['发布时间']}")
            st.info(current['内容'])

            st.markdown("### 🧠 AI 深度分析")
            if st.button("✨ 挖掘概念与龙头", type="primary"):
                with st.spinner("AI 正在分析..."):
                    try:
                        llm = ChatOpenAI(
                            api_key=api_key,
                            base_url="https://open.bigmodel.cn/api/paas/v4/",
                            model="glm-4-flash",
                            temperature=0.3
                        )
                        prompt = ChatPromptTemplate.from_messages([
                            ("system", "你是专业分析师。请提取新闻中的核心产业链概念，并挖掘3只相关龙头股。使用Markdown格式。"),
                            ("user", "标题：{title}\n内容：{content}")
                        ])
                        chain = prompt | llm | StrOutputParser()
                        res = chain.invoke({"title": current['标题'], "content": current['内容']})
                        st.success("分析完成")
                        st.markdown(res)
                    except Exception as e:
                        st.error(f"分析出错: {e}")

# ================= 3. 功能函数：市场强势股 (RPS) =================
def render_strong_page():
    st.header("🔥 市场强势股 (RPS 信号池)")
    st.caption("策略：陶博士 RPS 三线红 (>87) | 数据源：Tushare Pro | 更新时间：每日 17:00")

    # 读取 GitHub 上的数据
    csv_path = "data/strong_stocks.csv"
    
    if not os.path.exists(csv_path):
        st.warning(f"⚠️ 尚未检测到数据文件 ({csv_path})。请确认你是否已将本地生成的 csv 上传到 GitHub 的 data 文件夹。")
        return

    try:
        df = pd.read_csv(csv_path)
    except:
        st.error("数据读取失败，请检查 CSV 格式。")
        return

    if df.empty:
        st.info("今日无符合条件个股。")
        return

    # 数据概览
    update_date = df['更新日期'].iloc[0] if '更新日期' in df.columns else "未知"
    
    # 顶部指标卡
    k1, k2, k3 = st.columns(3)
    k1.metric("数据日期", update_date)
    k2.metric("强势股总数", f"{len(df)} 只")
    # 统计连续上榜超过10天的
    if '连续天数' in df.columns:
        super_stock = len(df[df['连续天数']>=10])
        k3.metric("🔥 妖股预备队 (>10天)", f"{super_stock} 只")

    st.markdown("---")

    # 筛选栏
    with st.expander("🔍 筛选工具", expanded=True):
        c1, c2 = st.columns([1, 2])
        min_days = c1.slider("最少连续上榜天数", 1, 30, 1)
        search = c2.text_input("搜索代码或名称")

    # 数据过滤
    mask = df['连续天数'] >= min_days
    if search:
        mask = mask & (df['ts_code'].str.contains(search) | df['name'].str.contains(search))
    
    filtered_df = df[mask].sort_values('RPS_50', ascending=False)

    # 漂亮的数据表
    st.dataframe(
        filtered_df[['ts_code', 'name', 'industry', 'close_now', 'RPS_50', 'RPS_120', 'RPS_250', '连续天数', '初次入选']],
        column_config={
            "ts_code": "代码",
            "name": "名称",
            "industry": "行业",
            "close_now": st.column_config.NumberColumn("现价", format="¥ %.2f"),
            "RPS_50": st.column_config.ProgressColumn("RPS 50 (短期)", min_value=80, max_value=100, format="%.1f"),
            "RPS_120": st.column_config.NumberColumn("RPS 120 (中期)", format="%.1f"),
            "RPS_250": st.column_config.NumberColumn("RPS 250 (长期)", format="%.1f"),
            "连续天数": st.column_config.NumberColumn("连续在榜", format="%d 天"),
        },
        use_container_width=True,
        hide_index=True,
        height=800
    )

# ================= 4. 主程序入口与导航 =================
def main():
    with st.sidebar:
        st.title("Chilam.Club")
        st.markdown("“不得贪胜，步步登高”")
        
        # 导航菜单
        page = st.radio(
            "功能导航", 
            ["📰 实时新闻挖掘", "🔥 市场强势股 (VIP)"],
            index=1  # 默认显示强势股页面
        )
        
        st.markdown("---")
        st.info("数据说明：\nRPS > 87 为强势阈值\n三线红代表中长期趋势共振")

    if page == "📰 实时新闻挖掘":
        render_news_page()
    elif page == "🔥 市场强势股 (VIP)":
        render_strong_page()

if __name__ == "__main__":
    main()

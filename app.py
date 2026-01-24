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

# ... (请保留之前的 get_news_data 和 render_news_page 函数) ...
# 为了节省篇幅，这里假设你保留了 AI 新闻挖掘的代码
# ===================================================

# 3. 强势股页面 (精简版)
def render_strong_page():
    st.header("🔥 市场强势信号池 (RPS)")
    st.caption("策略：陶博士三线红 (>87) | 数据源：Tushare Pro | 每日17:00自动更新")

    df = load_data("data/strong_stocks.csv")
    
    if df is None or df.empty:
        st.info("📊 数据尚未初始化，请等待今日收盘后首次更新。")
        return

    # 顶部指标
    c1, c2, c3 = st.columns(3)
    c1.metric("入选数量", f"{len(df)} 只")
    c2.metric("妖股预备(>10天)", f"{len(df[df['连续天数']>=10])} 只")
    c3.markdown(f"**数据日期**: {df['更新日期'].iloc[0]}")
    
    st.markdown("---")

    # 筛选工具
    with st.expander("🔍 筛选工具", expanded=True):
        sc1, sc2 = st.columns([1,2])
        min_d = sc1.slider("至少连续上榜天数", 1, 30, 1)
        kw = sc2.text_input("搜索股票代码/名称")
    
    # 逻辑过滤
    mask = df['连续天数'] >= min_d
    if kw: 
        mask = mask & (df['ts_code'].str.contains(kw) | df['name'].str.contains(kw))
    
    show_df = df[mask].sort_values('RPS_50', ascending=False)

    # 展示表格
    st.dataframe(
        show_df[['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'RPS_120', 'RPS_250', '连续天数', 'eastmoney_url']],
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "eastmoney_url": st.column_config.LinkColumn(
                "详情", 
                display_text="K线➡️", 
                help="点击跳转东方财富"
            ),
            "price_now": st.column_config.NumberColumn("现价", format="¥ %.2f"),
            "RPS_50": st.column_config.ProgressColumn("RPS 50", min_value=80, max_value=100, format="%.1f"),
            "RPS_120": st.column_config.NumberColumn("RPS 120", format="%.1f"),
            "连续天数": st.column_config.NumberColumn("连续在榜", format="%d 天"),
        },
        use_container_width=True,
        hide_index=True,
        height=800
    )

# 4. 主程序导航
def main():
    with st.sidebar:
        st.title("Chilam.Club")
        st.markdown("“不得贪胜，步步登高”")
        page = st.radio("功能导航", ["📰 实时新闻挖掘", "🔥 市场强势股 (VIP)"], index=1)

    if page == "📰 实时新闻挖掘":
        # render_news_page()  <-- 记得把你的新闻函数取消注释
        st.write("请把原来的新闻代码放回这里") # 占位符
    elif page == "🔥 市场强势股 (VIP)":
        render_strong_page()

if __name__ == "__main__":
    main()

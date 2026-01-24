import streamlit as st
import pandas as pd
import akshare as st_ak
import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

st.set_page_config(page_title="Chilam Club - 投资驾驶舱", page_icon="🚀", layout="wide")

# ... (保留原有的 get_news_data 和 render_news_page 函数，不需要动) ...
# 为了篇幅，这里我省略了 get_news_data 和 render_news_page 的代码
# 请把你原来的这两个函数完整保留在这里！
# -----------------------------------------------------------

# === 新增：加载数据函数 ===
def load_data(path):
    if not os.path.exists(path): return None
    return pd.read_csv(path)

# === 升级版：强势股页面 ===
def render_strong_page():
    st.header("🔥 市场强势信号池 (RPS)")
    st.caption("策略：陶博士三线红 | 数据源：Tushare Pro | 每日17:00自动更新")

    tab1, tab2 = st.tabs(["🐉 个股龙虎榜", "🌋 强势板块"])

    # --- Tab 1: 个股 ---
    with tab1:
        df = load_data("data/strong_stocks.csv")
        if df is None or df.empty:
            st.info("数据暂未生成，请等待自动更新。")
        else:
            # 顶部指标
            c1, c2, c3 = st.columns(3)
            c1.metric("入选数量", f"{len(df)} 只")
            c2.metric("妖股(>10天)", f"{len(df[df['连续天数']>=10])} 只")
            c3.markdown(f"**数据日期**: {df['更新日期'].iloc[0]}")
            
            # 筛选
            with st.expander("🔍 筛选工具", expanded=True):
                sc1, sc2 = st.columns([1,2])
                min_d = sc1.slider("至少连续上榜天数", 1, 30, 1)
                kw = sc2.text_input("搜索股票")
            
            mask = df['连续天数'] >= min_d
            if kw: mask = mask & (df['ts_code'].str.contains(kw) | df['name'].str.contains(kw))
            show_df = df[mask].sort_values('RPS_50', ascending=False)

            # 配置链接列
            st.dataframe(
                show_df[['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'RPS_120', 'RPS_250', '连续天数', 'eastmoney_url']],
                column_config={
                    "ts_code": st.column_config.TextColumn("代码"),
                    "eastmoney_url": st.column_config.LinkColumn(
                        "详情链接", 
                        display_text="查看K线 ->", # 显示文字
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

    # --- Tab 2: 板块 ---
    with tab2:
        df_sec = load_data("data/strong_sectors.csv")
        if df_sec is None or df_sec.empty:
            st.warning("板块数据暂缺。")
        else:
            st.success(f"当前市场主线：共有 {len(df_sec)} 个一级行业进入强势区")
            st.dataframe(
                df_sec.sort_values('RPS_50', ascending=False),
                column_config={
                    "RPS_50": st.column_config.ProgressColumn("RPS 50 (板块强度)", min_value=85, max_value=100, format="%.1f"),
                },
                use_container_width=True,
                hide_index=True
            )

# ... (保留 main 函数，确保 render_strong_page 被调用) ...

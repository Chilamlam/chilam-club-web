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

# 美化 RPS (生成带箭头的列)
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

# ================= 新闻模块 =================
@st.cache_data(ttl=300)
def get_news_data():
    try:
        return st_ak.stock_info_global_cls()
    except: return pd.DataFrame({"标题": ["接口繁忙"], "发布日期": ["-"], "内容": ["请稍后..."]})

def render_news_page():
    st.header("📰 实时新闻挖掘")
    if "ZHIPU_API_KEY" in st.secrets: api_key = st.secrets["ZHIPU_API_KEY"]
    else: api_key = os.getenv("ZHIPU_API_KEY", "")

    with st.spinner('加载中...'): news_df = get_news_data()
    if 'selected_idx' not in st.session_state: st.session_state.selected_idx = 0
    
    c1, c2 = st.columns([3, 7])
    with c1:
        st.subheader("实时流")
        for idx, row in news_df.head(30).iterrows():
            if st.button(f"📄 {str(row['标题'])[:18]}...", key=f"n_{idx}", use_container_width=True, type="primary" if idx==st.session_state.selected_idx else "secondary"):
                st.session_state.selected_idx = idx
                st.rerun()
    with c2:
        if not news_df.empty:
            cur = news_df.iloc[st.session_state.selected_idx]
            st.markdown(f"### {cur['标题']}")
            st.caption(f"{cur['发布日期']}")
            st.info(cur['内容'])
            if st.button("✨ AI 分析", type="primary"):
                if not api_key: st.error("缺 API Key"); return
                with st.spinner("分析中..."):
                    llm = ChatOpenAI(api_key=api_key, base_url="https://open.bigmodel.cn/api/paas/v4/", model="glm-4-flash")
                    chain = ChatPromptTemplate.from_messages([("user", "分析新闻：{t}\n{c}\n给出利好/利空及相关A股龙头。")]) | llm | StrOutputParser()
                    st.markdown(chain.invoke({"t": cur['标题'], "c": cur['内容']}))

# ================= 个股页面 (修复展示) =================
def render_stock_content(df):
    if df is None or df.empty: st.info("暂无数据"); return
    
    c1, c2, c3 = st.columns(3)
    c1.metric("入选", f"{len(df)} 只")
    c3.markdown(f"**更新**: {df['更新日期'].iloc[0] if '更新日期' in df.columns else '-'}")
    
    with st.expander("🔍 筛选", expanded=True):
        sc1, sc2, sc3 = st.columns([1, 1, 1])
        min_d = sc1.slider("连榜天数", 1, 30, 1)
        # 题材筛选
        opts = ["全部"] + sorted([x for x in df['细分行业'].dropna().unique() if x != '-']) if '细分行业' in df.columns else ["全部"]
        ind = sc2.selectbox("题材", opts)
        kw = sc3.text_input("搜索")

    mask = df['连续天数'] >= min_d
    if ind != "全部": mask &= (df['细分行业'] == ind)
    if kw: mask &= (df['ts_code'].astype(str).str.contains(kw) | df['name'].str.contains(kw))
    
    show_df = df[mask].sort_values('RPS_50', ascending=False).copy()
    show_df = format_rps_show(show_df, 'RPS_50', 'rps_50_chg')

    # ★ 强制指定显示列 (排除 rps_50_chg)
    cols = ['ts_code', 'name', '细分行业', 'price_now', 'RPS_50_Show', 'RPS_120', 'RPS_250', '连续天数', 'xueqiu_url']
    final_cols = [c for c in cols if c in show_df.columns]

    st.dataframe(
        show_df[final_cols],
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "RPS_50_Show": st.column_config.TextColumn("RPS 50 (变化)"),
            "细分行业": st.column_config.TextColumn("题材"),
            "price_now": st.column_config.NumberColumn("现价", format="%.2f"),
        },
        use_container_width=True, hide_index=True, height=800
    )

# ================= ETF 页面 (修复冗余列) =================
def render_etf_content(df):
    if df is None or df.empty: st.info("暂无数据"); return
    
    st.success(f"📈 捕捉到 {len(df)} 只强势 ETF")
    kw = st.text_input("🔍 搜 ETF")
    show_df = df.copy()
    if kw: show_df = show_df[show_df['name'].str.contains(kw) | show_df['ts_code'].str.contains(kw)]
    
    show_df = format_rps_show(show_df, 'RPS_50', 'rps_50_chg')
    
    # ★ 强制指定显示列 (去掉 rps_50_chg, RPS_50 等中间变量)
    target_cols = ['ts_code', 'name', 'price_now', 'RPS_50_Show', 'RPS_120', 'RPS_250', 'xueqiu_url']
    final_cols = [c for c in target_cols if c in show_df.columns]

    st.dataframe(
        show_df[final_cols],
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "RPS_50_Show": st.column_config.TextColumn("RPS 50 (变化)"),
            "price_now": st.column_config.NumberColumn("现价", format="%.3f"),
        },
        use_container_width=True, hide_index=True, height=800
    )

def main():
    with st.sidebar:
        st.title("Chilam.Club")
        page = st.radio("导航", ["📰 新闻挖掘", "🔥 强势股 (VIP)"], index=1)
        st.divider()
        # ★★★ 修复乱码：改回标准 if 语句 ★★★
        if os.path.exists("donate.jpg"):
            st.image("donate.jpg", caption="请喝咖啡 ☕")

    if page == "📰 新闻挖掘": render_news_page()
    else:
        df_stock = load_data("data/strong_stocks.csv")
        df_etf = load_data("data/strong_etfs.csv")
        t1, t2 = st.tabs(["个股", "ETF"])
        with t1: render_stock_content(df_stock)
        with t2: render_etf_content(df_etf)

if __name__ == "__main__":
    main()

import streamlit as st
import pandas as pd
from langchain_openai import ChatOpenAI
# 👇 修改了下面这行，改用 langchain_core
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import akshare as st_ak

# ================= 配置部分 =================
st.set_page_config(
    page_title="Chilam Club - AI 财经终端",
    page_icon="📰",
    layout="wide"
)

# ================= 数据获取层 =================
@st.cache_data(ttl=300) # 增加缓存，每5分钟才拉一次新数据，防止被封IP
def get_news_data():
    try:
        # 尝试获取全球财经新闻
        df = st_ak.stock_info_global_cls()
        return df
    except Exception as e:
        st.error(f"数据源暂时不可用，正在使用模拟数据: {e}")
        # 如果 Akshare 失败，返回一个模拟数据防止程序崩溃
        mock_data = {
            "标题": ["测试新闻：某科技巨头发布新一代AI芯片", "测试新闻：新能源汽车销量大涨"],
            "发布日期": ["2026-01-24", "2026-01-24"],
            "发布时间": ["10:00:00", "11:30:00"],
            "内容": ["某公司今日发布了最新一代GPU，算力提升30%...", "乘联会数据显示，本月新能源车渗透率突破50%..."]
        }
        return pd.DataFrame(mock_data)

def app():
    # ================= 界面布局 =================
    st.title("🤖 新闻概念挖掘[免费服务5分钟更新一次]")
    st.caption("Powered by Chilam Club")

    # ================= 安全获取 API Key =================
    # 从 Streamlit Secrets 获取 Key，不再硬编码
    if "ZHIPU_API_KEY" in st.secrets:
        api_key = st.secrets["ZHIPU_API_KEY"]
    else:
        st.error("请在 Streamlit 后台 Settings -> Secrets 中配置 ZHIPU_API_KEY")
        st.stop()

    # ================= 主程序逻辑 =================
    # 加载数据
    with st.spinner('正在连接全球财经资讯...'):
        news_df = get_news_data()

    # 初始化 Session State 用于存储选中的新闻
    if 'selected_idx' not in st.session_state:
        st.session_state.selected_idx = 0

    # 布局：左侧新闻列表，右侧详情与分析
    col_list, col_detail = st.columns([3, 7])

    with col_list:
        st.subheader("📰 实时新闻流")
        # 显示新闻列表
        for idx, row in news_df.iterrows():
            with st.container():
                # 高亮选中项
                if idx == st.session_state.selected_idx:
                    status = "primary" # 选中状态颜色
                else:
                    status = "secondary" # 普通状态
                
                # 点击事件
                btn_label = f"{row['标题'][:15]}..." # 缩短标题防止太长
                if st.button(
                    f"📄 {row['标题']}", 
                    key=f"news_{idx}", 
                    use_container_width=True,
                    type=status
                ):
                    st.session_state.selected_idx = idx
                    st.rerun()

    with col_detail:
        # 获取当前选中的新闻
        if not news_df.empty:
            current_news = news_df.iloc[st.session_state.selected_idx]
            
            st.markdown("---")
            # 1. 展示新闻原文
            st.subheader(f"📌 {current_news['标题']}")
            st.caption(f"发布时间：{current_news['发布日期']} {current_news['发布时间']}")
            st.info(current_news['内容'])

            # 2. AI 分析按钮
            st.markdown("### 🧠 AI 深度分析")
            if st.button("✨ 开始分析：提取概念 & 挖掘个股", type="primary"):
                with st.spinner("AI 分析师正在阅读新闻并进行逻辑推理..."):
                    try:
                        # 初始化 LLM (智谱AI)
                        llm = ChatOpenAI(
                            api_key=api_key,
                            base_url="https://open.bigmodel.cn/api/paas/v4/",
                            model="glm-4-flash",
                            temperature=0.3
                        )

                        # 构建 Prompt
                        prompt = ChatPromptTemplate.from_messages([
                            ("system", "你是一位专业的财经证券分析师。请阅读用户提供的财经新闻，完成以下任务：\n"
                                     "0. **情绪识别**：分析该新闻的内容到底是利好还是利空。\n"
                                     "1. **概念识别**：分析该新闻涉及的核心产业链概念（例如：Robotaxi, CPO, 创新药等）。\n"
                                     "2. **个股挖掘**：根据概念，列出3-5只A股或港股中最相关的龙头个股名称，并用一句话解释关联理由。\n\n"
                                     "输出格式请使用 Markdown，清晰分级。"),
                            ("user", "新闻标题：{title}\n\n新闻内容：{content}\n\n请开始分析。")
                        ])

                        chain = prompt | llm | StrOutputParser()

                        # 调用模型
                        analysis_result = chain.invoke({
                            "title": current_news['标题'],
                            "content": current_news['内容']
                        })

                        # 展示结果
                        st.success("分析完成！")
                        st.markdown(analysis_result)

                    except Exception as e:
                        st.error(f"分析过程出错: {e}")
        else:
            st.warning("暂无新闻数据")

if __name__ == "__main__":
    app()



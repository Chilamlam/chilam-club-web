import streamlit as st
import pandas as pd

# 页面配置
st.set_page_config(page_title="Chilam Club", page_icon="??")

# 标题
st.title("Welcome to Chilam.club")

# 副标题
st.subheader("不得贪胜，步步登高")

# 模拟一个简单的功能（结合你的股票兴趣）
st.write("### ?? 每日观察")
data = {
    '名称': ['转债A', '转债B', '股票C'],
    '价格': [105.2, 110.5, 23.8],
    '涨跌幅': ['+0.5%', '-0.2%', '+1.2%']
}
df = pd.DataFrame(data)
st.dataframe(df)

# 模拟一个吉他板块
st.write("---")
st.write("### ?? 吉他装备库")

st.text("当前主力: Ibanez RG550 (日产)")

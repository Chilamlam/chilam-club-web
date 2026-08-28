"""
会员 Dashboard 页面
展示用户订阅状态与 VIP 特权
"""
import streamlit as st
from datetime import datetime
import auth
import database

st.set_page_config(page_title="会员中心 - Chilam Club", page_icon="👤", layout="centered")

st.title("👤 会员中心")

if not auth.is_logged_in():
    st.warning("⚠️ 请先登录后再查看会员中心")
    if st.button("去登录 / 注册 🔐", use_container_width=True):
        st.switch_page("pages/auth.py")
    st.stop()

user = auth.get_current_user()
email = user.get("email")
user_id = user.get("user_id")
is_admin = user.get("is_admin", False)

st.markdown(f"### 欢迎，`{email}`")

# 获取订阅详情
sub = database.get_active_subscription(user_id) if not is_admin else None

with st.container(border=True):
    if is_admin:
        st.success("🌟 **超级管理员特权生效中**：拥有全站所有模块无限制访问权限。")
    elif sub:
        plan_name = auth.get_plan_display_name(sub.get("plan_name", ""))
        expires_at = sub.get("expires_at", "")[:10]
        st.success(f"👑 **VIP 会员生效中**")
        st.markdown(f"- **套餐类型**: {plan_name}")
        st.markdown(f"- **到期时间**: `{expires_at}`")
    else:
        st.warning("🔒 **当前状态: 免费用户 (未激活 VIP)**")
        st.markdown("升级 VIP 可解锁：**🔥 强势股 (RPS 动量)**、**⚡ 投机与套利 (可转债双低/溢价)**、**📚 投资作业本 (顶尖机构持仓追踪)** 等核心功能。")

st.markdown("---")
st.subheader("💳 VIP 订阅方案")

cols = st.columns(3)
plans = [
    ("月度 VIP", "¥25", "适合短期尝鲜体验", "monthly"),
    ("季度 VIP", "¥60", "立省 15 元 · 季度优选", "quarterly"),
    ("年度 VIP", "¥200", "超值省 100 元 · 全年无忧", "yearly")
]

for idx, (p_title, p_price, p_desc, p_key) in enumerate(plans):
    with cols[idx]:
        with st.container(border=True):
            st.markdown(f"#### {p_title}")
            st.markdown(f"## {p_price}")
            st.caption(p_desc)
            st.markdown("---")
            st.info("如需开通，请联系管理员微信或发送邮件")

st.markdown("---")
st.caption("💡 订阅开通或问题反馈，请联系管理员微信。")

c1, c2 = st.columns(2)
with c1:
    if st.button("⬅️ 返回主页", use_container_width=True):
        st.switch_page("app.py")
with c2:
    if st.button("🚪 退出登录", use_container_width=True):
        auth.logout()
        st.switch_page("app.py")

"""
登录与注册页面
"""
import streamlit as st
import auth

st.set_page_config(page_title="会员登录 / 注册 - Chilam Club", page_icon="🔐", layout="centered")

st.title("🔐 Chilam Club 会员系统")

if auth.is_logged_in():
    user = auth.get_current_user()
    st.success(f"已登录账号：{user.get('email')}")
    if auth.is_admin():
        st.info("身份：超级管理员 🛡️")
    elif auth.is_vip():
        st.info("身份：VIP 会员 👑")
    else:
        st.warning("身份：普通用户（未开通 VIP）")
        
    c1, c2 = st.columns(2)
    with c1:
        if st.button("进入会员中心 👤", use_container_width=True):
            st.switch_page("pages/dashboard.py")
    with c2:
        if st.button("退出登录 🚪", use_container_width=True):
            auth.logout()
            st.rerun()
    st.stop()

tab_login, tab_register = st.tabs(["🔑 用户登录", "📝 新用户注册"])

with tab_login:
    with st.form("login_form"):
        email = st.text_input("邮箱地址", placeholder="your_email@example.com")
        password = st.text_input("密码", type="password")
        submit_login = st.form_submit_button("立即登录 🚀", use_container_width=True)
        
        if submit_login:
            if not email or not password:
                st.error("请完整填写邮箱和密码")
            else:
                ok, err = auth.login(email, password)
                if ok:
                    st.success("登录成功！正在跳转...")
                    st.rerun()
                else:
                    st.error(f"登录失败: {err}")

with tab_register:
    with st.form("register_form"):
        reg_email = st.text_input("邮箱地址", placeholder="your_email@example.com")
        reg_password = st.text_input("设置密码 (至少6位)", type="password")
        reg_password2 = st.text_input("确认密码", type="password")
        submit_reg = st.form_submit_button("立即注册 🎁", use_container_width=True)
        
        if submit_reg:
            if reg_password != reg_password2:
                st.error("两次输入的密码不一致")
            else:
                ok, err = auth.register(reg_email, reg_password)
                if ok:
                    st.success("注册成功并已自动登录！")
                    st.rerun()
                else:
                    st.error(f"注册失败: {err}")

st.markdown("---")
if st.button("⬅️ 返回主页", use_container_width=True):
    st.switch_page("app.py")

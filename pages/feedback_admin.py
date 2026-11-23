"""Standalone feedback inbox with fresh database-backed admin verification."""
import os
import sys

import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import auth
import account_shell
import database
import feedback_ui


account_shell.configure_page(
    current="feedback_admin",
    page_title="反馈收件箱 - Chilam Club",
    page_icon="💬",
    layout="wide",
)

st.title("💬 用户反馈收件箱")

if not auth.is_logged_in():
    st.warning("请先登录管理员账号")
    if st.button("去登录 🔐"):
        st.switch_page("pages/auth.py")
    st.stop()

try:
    user_id = auth.get_user_id()
except Exception:
    user_id = None

verified_user, verify_err = database.get_user_by_id_checked(user_id)
if verify_err:
    st.error("无法从账号表验证当前管理员权限，反馈读取已停止。请稍后重试。")
    st.stop()
if not verified_user or verified_user.get("is_admin") is not True:
    st.error("当前账号的管理员权限已失效，反馈读取已拒绝。")
    st.stop()

admin_email = str(verified_user.get("email") or "管理员")
st.caption(f"当前管理员：{admin_email}")
st.caption("反馈只在管理员打开本页时按状态和分页从服务器读取，不会自动发送到外部消息平台。")

feedback_ui.render_admin_feedback(handled_by=admin_email)

st.markdown("---")
if st.button("⬅️ 返回后台管理"):
    st.switch_page("pages/admin.py")

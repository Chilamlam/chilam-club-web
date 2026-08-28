"""
后台管理页面 - 管理员查看用户列表与手动开通 VIP
"""
import streamlit as st
import auth
import database

st.set_page_config(page_title="后台管理 - Chilam Club", page_icon="⚙️", layout="wide")

if not auth.is_logged_in():
    st.warning("请先登录管理员账号")
    if st.button("去登录 🔐"):
        st.switch_page("pages/auth.py")
    st.stop()

if not auth.is_admin():
    st.error("❌ 拒绝访问：您不是管理员。")
    if st.button("返回主页 🏠"):
        st.switch_page("app.py")
    st.stop()

st.title("⚙️ Chilam Club 后台管理")
st.caption(f"当前管理员: {auth.get_user_email()}")

tab_users, tab_grant = st.tabs(["👥 用户列表", "🎁 手动开通/续期 VIP"])

with tab_users:
    st.subheader("注册用户")
    users = database.get_all_users()
    if users:
        formatted_users = []
        for u in users:
            is_u_vip = database.is_vip_active(u["id"])
            formatted_users.append({
                "UID": u["id"],
                "邮箱": u["email"],
                "角色": "🛡️ 管理员" if u.get("is_admin") else "普通用户",
                "VIP状态": "👑 VIP有效" if (is_u_vip or u.get("is_admin")) else "免费用户",
                "注册时间": u.get("created_at", "")[:19].replace("T", " ")
            })
        st.dataframe(formatted_users, use_container_width=True, hide_index=True)
    else:
        st.info("暂无用户数据")

with tab_grant:
    st.subheader("给用户手动开通/延长 VIP")
    with st.form("grant_vip_form"):
        target_email = st.text_input("用户邮箱", placeholder="输入已注册用户的邮箱")
        plan = st.selectbox("开通套餐", ["monthly (月度/1个月)", "quarterly (季度/3个月)", "yearly (年度/12个月)"])
        submit_grant = st.form_submit_button("立即开通 / 续期 🚀")
        
        if submit_grant:
            if not target_email:
                st.error("请输入用户邮箱")
            else:
                user_obj = database.get_user_by_email(target_email)
                if not user_obj:
                    st.error(f"未找到邮箱为 `{target_email}` 的用户")
                else:
                    plan_map = {
                        "monthly (月度/1个月)": ("monthly", 1),
                        "quarterly (季度/3个月)": ("quarterly", 3),
                        "yearly (年度/12个月)": ("yearly", 12)
                    }
                    p_name, p_months = plan_map[plan]
                    res = database.manual_create_subscription(user_obj["id"], p_name, p_months)
                    if res:
                        st.success(f"成功为 `{target_email}` 开通 {p_name}，到期时间：{res.get('expires_at')[:10]}")
                    else:
                        st.error("开通失败，请检查数据库配置")

st.markdown("---")
if st.button("⬅️ 返回主页", use_container_width=False):
    st.switch_page("app.py")

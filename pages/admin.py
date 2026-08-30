"""
后台管理 - 用户列表、订单确认收款、手动开通/续期 VIP

位置约束（2026-08-30 修正，不要再搬回根目录）：
    本文件必须留在 `pages/` 下。Streamlit 的 `st.switch_page()` 只接受
    「入口脚本 app.py」或「pages/ 目录下的文件」，放在根目录的 admin.py
    不是一个被注册的页面，`st.switch_page("admin.py")` 会抛
    StreamlitAPIException（Could not find page），且 Cloud 上错误详情被脱敏，
    只能看到一条没有信息量的红框 —— 排查成本远高于这个文件放哪。
    副作用：pages/ 下的文件会出现在默认侧边栏导航里，因此本页开头的
    管理员校验是真正的门禁，不能删。
"""
import os
import sys

import streamlit as st

# pages/ 是 Streamlit 子页目录，运行时 sys.path[0] 未必是项目根，
# 显式补一次，与 pages/dashboard.py 保持一致。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import auth
import database

st.set_page_config(page_title="后台管理 - Chilam Club", page_icon="⚙️", layout="wide")

# ================= 权限检查 =================
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

admin_id = auth.get_user_id()

# ================= Tabs =================
tab_users, tab_orders, tab_grant = st.tabs([
    "👥 用户列表",
    "📦 订单确认收款",
    "🎁 手动开通/续期"
])

# ================= Tab 1: 用户列表 =================
with tab_users:
    st.subheader("注册用户")
    users = database.get_all_users()
    if users:
        formatted_users = []
        for u in users:
            is_u_vip = database.is_vip_active(u["id"])
            vip_detail = database.get_vip_status_detail(u["id"]) if not u.get("is_admin") else None
            remaining = vip_detail["remaining_days"] if vip_detail else None
            formatted_users.append({
                "UID": u["id"],
                "邮箱": u["email"],
                "角色": "🛡️ 管理员" if u.get("is_admin") else "普通用户",
                "VIP状态": "👑 VIP" if (is_u_vip or u.get("is_admin")) else "免费",
                "剩余天数": remaining if remaining is not None else ("∞" if u.get("is_admin") else "—"),
                "到期日": (vip_detail["expires_at"][:10] if vip_detail and vip_detail.get("expires_at") else "—") if not u.get("is_admin") else "—",
                "注册时间": u.get("created_at", "")[:19].replace("T", " ")
            })
        st.dataframe(formatted_users, use_container_width=True, hide_index=True)
    else:
        st.info("暂无用户数据")

# ================= Tab 2: 订单确认收款 =================
with tab_orders:
    payments_ready = database.check_payments_table()

    if not payments_ready:
        st.error("⚠️ payments 订单表尚未创建！")
        with st.expander("📋 点击查看建表 SQL（复制到 Supabase SQL Editor 执行）", expanded=True):
            sql_path = os.path.join(_ROOT, "init_payments_table.sql")
            if os.path.exists(sql_path):
                with open(sql_path, "r", encoding="utf-8") as f:
                    sql_content = f.read()
                st.code(sql_content, language="sql")
            else:
                st.warning("未找到 init_payments_table.sql 文件")
        st.stop()

    # --- 待处理订单 ---
    st.subheader("⏳ 待确认订单")
    pending_list = database.get_pending_payments()

    if pending_list:
        st.success(f"共有 **{len(pending_list)}** 笔待处理订单")
        for p in pending_list:
            u_info = database.get_user_by_id(p["user_id"]) if p.get("user_id") else None
            u_email = u_info.get("email", "—") if u_info else f"UID:{p.get('user_id')}"
            plan_d = auth.get_plan_display_name(p.get("plan_name", ""))
            created = p.get("created_at", "")[:19].replace("T", " ")

            with st.container(border=True):
                oc1, oc2, oc3, oc4 = st.columns([3, 2, 2, 1.5])
                with oc1:
                    st.markdown(f"**{p.get('order_no', '')}**")
                    st.caption(f"用户：`{u_email}` | 创建于 {created}")
                with oc2:
                    st.markdown(f"**套餐**：{plan_d}")
                    st.caption(f"时长：{p.get('months', 1)} 个月")
                with oc3:
                    st.markdown(f"**金额**：¥{p.get('amount', 0)}")
                    method_map = {"wechat": "微信支付", "alipay": "支付宝"}
                    st.caption(method_map.get(p.get("payment_method", "wechat"), "微信支付"))
                with oc4:
                    if st.button("✅ 确认收款", key=f"confirm_{p.get('id')}", use_container_width=True):
                        result = database.confirm_payment(p["id"], admin_id)
                        if result and result.get("ok"):
                            exp_date = result.get("expires_at", "")[:10]
                            st.success(f"✅ 收款确认成功！{u_email} 的 {plan_d} 已激活，到期日：{exp_date}")
                            st.balloons()
                        else:
                            err = result.get("error", "未知错误") if result else "操作失败"
                            st.error(f"确认失败：{err}")
                        st.rerun()

                    if st.button("❌ 取消订单", key=f"reject_{p.get('id')}", use_container_width=False):
                        database.cancel_payment(p["id"])
                        st.rerun()
    else:
        st.info("🎉 暂无待处理订单")

    # --- 已完成订单历史 ---
    st.markdown("---")
    with st.expander("📜 已完成订单历史", expanded=False):
        all_subs = database.get_all_subscriptions()
        if all_subs:
            hist_data = []
            for s in all_subs[:50]:
                u_info = database.get_user_by_id(s.get("user_id", 0))
                u_email = u_info.get("email", "—") if u_info else f"UID:{s.get('user_id')}"
                hist_data.append({
                    "邮箱": u_email,
                    "套餐": auth.get_plan_display_name(s.get("plan_name", "")),
                    "状态": s.get("status", ""),
                    "开始日": s.get("start_at", "")[:10],
                    "到期日": s.get("expires_at", "")[:10]
                })
            st.dataframe(hist_data, use_container_width=True, hide_index=True)
        else:
            st.caption("暂无订阅记录")

# ================= Tab 3: 手动开通/续期 =================
with tab_grant:
    st.subheader("给用户手动开通/延长 VIP")
    st.caption("此功能为补充手段，建议优先使用「订单确认」Tab 走标准付费流程")

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

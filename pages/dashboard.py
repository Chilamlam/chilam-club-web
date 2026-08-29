"""
会员中心 - VIP 订阅、付费入口、订单管理、剩余天数展示
"""
import streamlit as st
from datetime import datetime, timezone
import os
import auth
import database
from ui_compat import image_stretch

st.set_page_config(page_title="会员中心 - Chilam Club", page_icon="👑", layout="centered")

st.title("👑 会员中心")

# ================= 登录检查 =================
if not auth.is_logged_in():
    st.warning("⚠️ 请先登录后再查看会员中心")
    if st.button("去登录 / 注册 🔐", use_container_width=True):
        st.switch_page("pages/auth.py")
    st.stop()

user = auth.get_current_user()
email = user.get("email")
user_id = user.get("user_id")
is_admin_flag = user.get("is_admin", False)

st.markdown(f"### 欢迎，`{email}`")

# ================= 获取收款配置 =================
def get_payment_config():
    try:
        return dict(st.secrets["payment"]) if "payment" in st.secrets else {}
    except Exception:
        return {}

pay_config = get_payment_config()
WECHAT_QR = pay_config.get("wechat_qr", "")
ALIPAY_QR = pay_config.get("alipay_qr", "")
ADMIN_WX = pay_config.get("admin_wechat", "chilam_club")
ADMIN_EMAIL = pay_config.get("admin_email", "chilam@admin.com")

# ================= 1. 当前 VIP 状态 =================
st.markdown("---")
st.subheader("当前会员状态")

if is_admin_flag:
    st.success("🌟 **超级管理员** — 全站所有模块无限制访问权限")
else:
    vip = auth.get_vip_status()
    if vip["is_active"]:
        remaining = vip["remaining_days"]
        plan_display = auth.get_plan_display_name(vip.get("plan_name", ""))
        expires = vip.get("expires_at", "")[:10] if vip.get("expires_at") else "—"

        col_s1, col_s2, col_s3 = st.columns(3)
        with col_s1:
            st.metric("会员状态", "👑 VIP 有效")
        with col_s2:
            st.metric("套餐类型", plan_display)
        with col_s3:
            st.metric("剩余天数", f"{remaining} 天")

        st.caption(f"📅 到期时间：`{expires}`")

        # 到期进度条
        if remaining is not None:
            if remaining <= 7:
                st.error(f"⚠️ 您的 VIP 即将到期（剩余 {remaining} 天），建议尽快续费！")
                progress_val = remaining / 30
            elif remaining <= 30:
                st.warning(f"💡 您的 VIP 还有 {remaining} 天到期，可提前续费累加时长。")
                progress_val = min(remaining / 90, 1.0)
            else:
                st.info(f"✅ VIP 状态正常，剩余 {remaining} 天。")
                progress_val = min(remaining / 365, 1.0)
            st.progress(progress_val, text=f"到期进度参考")
    else:
        st.warning("🔒 **当前状态：免费用户（未激活 VIP）**")
        st.markdown("升级 VIP 可解锁：**强势股 RPS 动量**、**投机与套利**、**投资作业本**、**自选股雷达** 等核心功能。")

# ================= 2. 检测 payments 表 =================
payments_ready = database.check_payments_table()

# ================= 3. VIP 订阅方案 + 下单 =================
st.markdown("---")
st.subheader("💳 VIP 订阅方案")

plans = [
    ("monthly", "月度 VIP", 25, "短期尝鲜体验", 1),
    ("quarterly", "季度 VIP", 60, "省 15 元 · 季度优选", 3),
    ("yearly", "年度 VIP", 200, "超值省 100 · 全年无忧", 12),
]

cols = st.columns(3)
for idx, (p_key, p_title, p_price, p_desc, p_months) in enumerate(plans):
    with cols[idx]:
        is_featured = (p_key == "quarterly")
        with st.container(border=True):
            if is_featured:
                st.markdown(f"#### ⭐ {p_title}")
            else:
                st.markdown(f"#### {p_title}")
            st.markdown(f"## ¥{p_price}")
            st.caption(p_desc)
            st.markdown("---")
            btn_label = "续费开通 🚀" if (not is_admin_flag and auth.is_vip()) else "立即开通 🚀"
            if is_admin_flag:
                st.caption("管理员无需订阅")
            elif st.button(btn_label, key=f"btn_buy_{p_key}", use_container_width=True,
                           disabled=not payments_ready):
                # 生成订单
                new_payment = database.create_payment(user_id, p_key, p_months, p_price)
                if new_payment:
                    st.session_state["last_order"] = new_payment
                    st.rerun()
                else:
                    st.error("订单创建失败，请稍后重试")

if not payments_ready:
    with st.expander("⚠️ 付费系统初始化提示（管理员请看）", expanded=is_admin_flag):
        st.warning("订单数据库表尚未创建，付费功能暂不可用。")
        if is_admin_flag:
            st.markdown("**请按以下步骤初始化：**")
            st.markdown("1. 登录 [Supabase Dashboard](https://supabase.com/dashboard)")
            st.markdown("2. 进入项目 → **SQL Editor**")
            st.markdown("3. 复制并执行项目根目录的 `init_payments_table.sql` 文件内容")
            sql_path = "init_payments_table.sql"
            if os.path.exists(sql_path):
                with open(sql_path, "r", encoding="utf-8") as f:
                    sql_content = f.read()
                st.code(sql_content, language="sql")
                st.caption("复制以上 SQL 到 Supabase SQL Editor 执行即可")
            else:
                st.caption("项目根目录下未找到 init_payments_table.sql 文件")
        else:
            st.info("付费系统正在初始化中，如需开通请联系管理员。")

# ================= 4. 最近订单 + 收款码 =================
if "last_order" in st.session_state and st.session_state["last_order"]:
    order = st.session_state["last_order"]
    if isinstance(order, dict) and order.get("status") == "pending":
        st.markdown("---")
        st.subheader("🧾 待付款订单")
        with st.container(border=True):
            plan_display = auth.get_plan_display_name(order.get("plan_name", ""))
            col_o1, col_o2 = st.columns(2)
            with col_o1:
                st.markdown(f"**订单号**")
                st.code(order.get("order_no", ""), language=None)
                st.markdown(f"**套餐**：{plan_display}")
                st.markdown(f"**金额**：¥{order.get('amount', 0)}")
            with col_o2:
                st.markdown(f"**创建时间**：{order.get('created_at', '')[:19].replace('T',' ')}")
                st.markdown(f"**状态**：⏳ 待付款")

            st.markdown("---")
            st.markdown("### 📲 扫码付款")
            st.markdown(f"**付款时请备注订单号：** `{order.get('order_no', '')}`")

            qr_cols = st.columns(2)
            with qr_cols[0]:
                st.markdown("**微信支付**")
                wechat_found = WECHAT_QR and os.path.exists(WECHAT_QR)
                if wechat_found:
                    image_stretch(WECHAT_QR)
                elif WECHAT_QR:
                    image_stretch(WECHAT_QR)
                else:
                    st.info("收款码待配置，请联系管理员")

            with qr_cols[1]:
                st.markdown("**支付宝**")
                alipay_found = ALIPAY_QR and os.path.exists(ALIPAY_QR)
                if alipay_found:
                    image_stretch(ALIPAY_QR)
                elif ALIPAY_QR:
                    image_stretch(ALIPAY_QR)
                else:
                    st.info("收款码待配置，请联系管理员")

            st.markdown("---")
            st.info(
                f"📋 **付款流程**：扫码付款（备注订单号）→ 管理员确认收款 → VIP 自动激活\n\n"
                f"💬 付款后请微信联系管理员（`{ADMIN_WX}`）或发邮件至 `{ADMIN_EMAIL}` 告知已付款\n\n"
                f"🔄 确认后刷新本页即可看到 VIP 生效"
            )

            if st.button("❌ 取消此订单", key="cancel_order"):
                database.cancel_payment(order.get("id"))
                st.session_state["last_order"] = None
                st.rerun()

# ================= 5. 我的待处理订单 =================
if payments_ready and not is_admin_flag:
    st.markdown("---")
    st.subheader("📋 我的待处理订单")
    pending_list = database.get_user_pending_payments(user_id)
    if pending_list:
        for p in pending_list:
            with st.container(border=True):
                plan_d = auth.get_plan_display_name(p.get("plan_name", ""))
                created = p.get("created_at", "")[:19].replace("T", " ")
                pc1, pc2, pc3 = st.columns([2, 1, 1])
                with pc1:
                    st.markdown(f"**{p.get('order_no', '')}** · {plan_d} · ¥{p.get('amount', 0)}")
                    st.caption(f"创建于 {created}")
                with pc2:
                    st.markdown("⏳ 待确认")
                with pc3:
                    if st.button("复制订单号", key=f"copy_{p.get('id')}"):
                        st.code(p.get("order_no", ""), language=None)
    else:
        st.caption("暂无待处理订单")

# ================= 6. 订单历史 =================
if payments_ready and not is_admin_flag:
    with st.expander("📜 全部订单历史", expanded=False):
        all_payments = database.get_user_payments(user_id)
        if all_payments:
            history_data = []
            for p in all_payments:
                status_map = {"pending": "⏳ 待付款", "completed": "✅ 已完成", "cancelled": "❌ 已取消"}
                history_data.append({
                    "订单号": p.get("order_no", ""),
                    "套餐": auth.get_plan_display_name(p.get("plan_name", "")),
                    "金额": f"¥{p.get('amount', 0)}",
                    "状态": status_map.get(p.get("status"), p.get("status", "")),
                    "创建时间": p.get("created_at", "")[:19].replace("T", " "),
                    "确认时间": (p.get("confirmed_at", "") or "")[:19].replace("T", " ") if p.get("confirmed_at") else "—"
                })
            st.dataframe(history_data, use_container_width=True, hide_index=True)
        else:
            st.caption("暂无订单记录")

# ================= 底部操作 =================
st.markdown("---")
c1, c2 = st.columns(2)
with c1:
    if st.button("⬅️ 返回主页", use_container_width=True):
        st.switch_page("app.py")
with c2:
    if st.button("🚪 退出登录", use_container_width=True):
        auth.logout()
        st.switch_page("app.py")

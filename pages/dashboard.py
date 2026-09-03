"""
会员中心 - VIP 订阅、付费入口、订单管理、剩余天数展示
"""
import streamlit as st
from datetime import datetime, timezone
import os
import sys
import auth
import database
from ui_compat import image_stretch

# pages/ 是 Streamlit 的子页目录，运行时 sys.path[0] 未必是项目根，
# 显式补一次，否则 admin_notify / push_binding 这类根目录模块导不进来。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import admin_notify
import push_binding as pb


def _bridge_secrets_to_env() -> None:
    """把告警通道凭据从 st.secrets 桥到环境变量。

    admin_notify 不 import streamlit（要在 Actions 里跑），只认环境变量；
    而站内运行时凭据在 st.secrets。已存在则不覆盖。
    """
    for name in ("DIGEST_SERVERCHAN_KEY", "WXPUSHER_APP_TOKEN"):
        if os.getenv(name):
            continue
        try:
            val = str(st.secrets.get(name, "")).strip()
        except Exception:
            val = ""
        if val:
            os.environ[name] = val
    pb.ensure_app_token()


def _notify_new_order(order: dict, user_email: str) -> None:
    """下单即告警。送达与否都要如实告诉用户。

    为什么把结果显示给用户看：这一步决定了他要不要自己去戳管理员。
    通道失败却提示"已通知"，等于让他安心地白等——付费流程里最伤信任的一种。
    """
    _bridge_secrets_to_env()
    title = f"💰 新订单待确认 ¥{order.get('amount', 0)} · {order.get('order_no', '')}"
    body = (
        "## 有新的付款订单\n\n"
        f"- 订单号：`{order.get('order_no', '')}`\n"
        f"- 用户：{user_email}（user_id={order.get('user_id')}）\n"
        f"- 套餐：{auth.get_plan_display_name(order.get('plan_name', ''))}"
        f"（{order.get('months', '')} 个月）\n"
        f"- 金额：¥{order.get('amount', 0)}\n"
        f"- 创建时间：{str(order.get('created_at', ''))[:19].replace('T', ' ')}\n\n"
        "收到款后请到「后台管理 → 待确认订单」点确认，VIP 会自动续期累加。"
    )
    try:
        ok, note = admin_notify.notify_admins(title, body)
    except Exception as e:
        ok, note = False, f"{type(e).__name__}: {e}"
    st.session_state["last_order_alert"] = (ok, note)

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
ADMIN_WX = pay_config.get("admin_wechat", "")
# 对外联系邮箱：**没配就不显示**，绝不回落到某个真实账号。
# 原实现把管理员的**登录邮箱**当默认值写死在这里（public 仓库），
# 等于对所有访客公布「后台账号是谁」—— 配上口令就是完整入侵链。
# 这一项要填的是专门的客服邮箱，**不能复用任何能登录本站的账号**。
ADMIN_EMAIL = pay_config.get("admin_email", "")


def _contact_hint(prefix: str = "") -> str:
    """拼「怎么联系管理员」这句话。渠道一个都没配时**不能装作有** ——
    否则用户看到「请联系 ``」这种空反引号，会以为页面坏了，
    更糟的是他会以为自己已经通知过了、然后一直等。"""
    ways = []
    if ADMIN_WX:
        ways.append(f"微信 `{ADMIN_WX}`")
    if ADMIN_EMAIL:
        ways.append(f"邮箱 `{ADMIN_EMAIL}`")
    if not ways:
        # 注意不要拼上 prefix（"付款后" + "⚠️ 尚未配置…" 读起来是病句）。
        return ("⚠️ 本站尚未配置管理员联系方式（Secrets 的 "
                "`[payment].admin_wechat` / `admin_email`），"
                "**你的付款暂时没有渠道可以告知** —— 请先不要付款。")
    return f"{prefix}请通过 {' 或 '.join(ways)} 告知订单号。"

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
        # 注意：这里只能写「开通后真的会变化」的东西。
        # 旧文案曾承诺解锁强势股/投机套利/作业本/自选股雷达，但这些页面早已全部开放，
        # 付费用户开通后会发现「什么都没变」——比不做付费墙更伤信任，故改为如实描述。
        st.markdown(
            "站内**所有行情与判断页面都免费开放**（全市场看板、板块轮动、收盘摘要正文、"
            "强势股、投机与套利、投资作业本、战绩回看等），无需开通即可查看。\n\n"
            "VIP 权益只有一项：**收盘摘要主动推送到微信**"
            "（含「你的池子今日表现」个性化段落），开通后需回本页扫码绑定一次微信。"
        )

# ================= 1.5 推送绑定（会员权益的第二个开关） =================
# 放在会员状态正下方，是因为这里是「刚付完钱、以为一切就绪」的那个位置。
# 之前绑定入口只在「我的池子」页一个默认折叠的 expander 里，付费用户走完
# 全流程一次都不会被告知还有这一步，结果权限开了、推送永远不来。
_bridge_secrets_to_env()
_member_now = is_admin_flag or auth.is_vip()
if _member_now:
    st.markdown("---")
    st.subheader("📲 收盘摘要推送")
    _bound_ok = pb.render_gate(
        user_id, key_prefix="dash",
        context="这是会员权益的一部分，与访问权限分开：权限已开通，投递还需要绑定一次微信。")
    if _bound_ok:
        with st.expander("管理微信推送绑定", expanded=False):
            pb.render(user_id, key_prefix="dash_mgr")
else:
    st.caption("💡 VIP 权益含「收盘后摘要自动推到微信」，开通后回到本页扫码绑定一次即可。")

# ================= 2. 检测 payments 表 =================
payments_ready = database.check_payments_table()

# ================= 3. VIP 订阅方案 + 下单 =================
st.markdown("---")
st.subheader("💳 VIP 订阅方案")

# 下单按钮正上方必须如实写清「买到什么、买不到什么」。
# 这是用户掏钱前看到的最后一段文字，写虚了就是事后退款与信任崩塌的源头。
st.info(
    "**开通后你会得到**：每个交易日收盘后，当日摘要自动推送到微信，"
    "并附「你的池子今日」个性化段落（中位涨幅、最强最弱、命中榜单）、"
    "明日验证条件带今日基准值可对账。\n\n"
    "**不包含（因为本来就免费）**：站内所有行情与判断页面均对所有人开放，"
    "开通 VIP 不会解锁任何新页面。"
)
st.caption("⚠️ 本站只提供数据与统计，不推荐个股、不提供买卖价位与仓位建议。")

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
                    # 立刻告警：否则订单会一直躺在表里等站长自己想起来看后台
                    _notify_new_order(new_payment, email)
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
            # 告警结果如实播报：决定用户要不要自己去戳管理员
            alert = st.session_state.get("last_order_alert")
            if alert is not None:
                ok_alert, note_alert = alert
                if ok_alert:
                    st.success("🔔 已自动通知管理员这笔订单，收到款后会尽快为你激活。")
                else:
                    st.warning(
                        "⚠️ 自动通知管理员**未成功**，这笔订单可能不会被及时看到。"
                        + _contact_hint("付款后务必主动"))
                    with st.expander("通知失败详情（管理员排查用）", expanded=False):
                        st.code(str(note_alert), language=None)

            st.info(
                f"📋 **付款流程**：扫码付款（备注订单号）→ 管理员确认收款 → VIP 自动激活\n\n"
                f"💬 {_contact_hint('付款后')}\n\n"
                f"🔄 确认后刷新本页即可看到 VIP 生效"
            )

            # 取消结果用 flash 传递：按钮下面紧跟 st.rerun()，
            # 直接 st.error 会在重跑时瞬间消失，用户会以为点了没反应
            _cflash = st.session_state.pop("cancel_flash", None)
            if _cflash:
                (st.success if _cflash[0] == "ok" else st.error)(_cflash[1])

            if st.button("❌ 取消此订单", key="cancel_order"):
                ok = database.cancel_payment(order.get("id"))
                st.session_state["cancel_flash"] = (
                    ("ok", "已取消订单。") if ok else
                    ("err", "⚠️ 取消未生效：云端没有更新到任何一行，订单仍是待处理。"
                            "请刷新后再试，或联系管理员处理。"))
                st.session_state["last_order"] = None
                st.session_state.pop("last_order_alert", None)
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

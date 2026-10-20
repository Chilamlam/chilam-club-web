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

# ── 导入引导：项目根**必须**被强制顶到 sys.path[0] ─────────────────────────
#   Streamlit 每次执行脚本前会跑 modified_sys_path（streamlit/runtime/
#   scriptrunner/exec_code.py:63）：把**本脚本所在目录**插到 sys.path[0]，
#   脚本跑完再摘掉。所以在 AppTest 里、或有人直接 `streamlit run pages/xxx.py`
#   时，sys.path[0] 是 pages/ 而不是项目根 —— 而 pages/auth.py 与根目录的
#   认证模块词干撞名（auth.py），`import auth` 会解析回**本页自己**：
#   先报 partially initialized module 'auth'，一旦有人试图「修掉」那个错位
#   登记，就变成 RecursionError 无限自执行（实测 162 层，2026-09-23）。
#   ★ 不能写成 `if _ROOT not in sys.path: insert`：根**通常已经在** sys.path
#     里（Streamlit 的 web/bootstrap.py 插过一次），条件不成立就不插，
#     pages/ 仍稳坐第一位，坑照旧。必须无条件移除再插到最前。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import auth
import database
import site_settings

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
tab_users, tab_orders, tab_grant, tab_reset, tab_site = st.tabs([
    "👥 用户列表",
    "📦 订单确认收款",
    "🎁 手动开通/续期",
    "🔑 密码重置",
    "🧩 站点设置"
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

    # 操作结果用 flash 传递：确认/取消都紧跟 st.rerun()，
    # 直接 st.error/st.success 会在重跑时瞬间消失，管理员看到的只是「点了没反应」。
    # 失败提示尤其重要——「订阅已创建但订单仍 pending，不要重复确认」这句
    # 看不见的话，下一步就是重复续期。
    _flash = st.session_state.pop("admin_flash", None)
    if _flash:
        (st.success if _flash[0] == "ok" else st.error)(_flash[1])

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
                            st.session_state["admin_flash"] = (
                                "ok", f"✅ 收款确认成功！{u_email} 的 {plan_d} 已激活，到期日：{exp_date}")
                        else:
                            err = result.get("error", "未知错误") if result else "操作失败"
                            st.session_state["admin_flash"] = ("err", f"确认失败：{err}")
                        st.rerun()

                    if st.button("❌ 取消订单", key=f"reject_{p.get('id')}", use_container_width=False):
                        ok = database.cancel_payment(p["id"])
                        st.session_state["admin_flash"] = (
                            ("ok", f"已取消订单 {p.get('order_no', '')}。") if ok else
                            ("err", "⚠️ 取消未生效：云端没有更新到任何一行，订单仍是原状态。请刷新后重试。"))
                        st.rerun()
    else:
        st.info("🎉 暂无待处理订单")

    # --- 已超时作废的订单（可恢复）---
    # 自动作废只说明「超时未确认」，不说明没付款。若用户其实付了款、只是
    # 管理员确认晚了，这里必须是能把权益找回来的入口，否则这笔钱变死账。
    expired_list = database.get_expired_payments(days=14)
    if expired_list:
        with st.expander(f"⌛ 已超时作废的订单（近 14 天 · {len(expired_list)} 笔）",
                         expanded=False):
            st.caption(
                "这些订单超过 24 小时未确认收款，已被系统自动作废并从上面的待确认列表移出。"
                "**若核对收款码账单后发现确实收到过款**，点「↩️ 恢复为待确认」把它退回待确认"
                "列表，再用「✅ 确认收款」开通 VIP；确实没收到款的不用管。"
                "（用户自己取消的订单不在此列，那是主动撤回意向。）"
            )
            for p in expired_list:
                u_info = database.get_user_by_id(p["user_id"]) if p.get("user_id") else None
                u_email = u_info.get("email", "—") if u_info else f"UID:{p.get('user_id')}"
                plan_d = auth.get_plan_display_name(p.get("plan_name", ""))
                created = p.get("created_at", "")[:19].replace("T", " ")

                with st.container(border=True):
                    ec1, ec2, ec3 = st.columns([3, 2, 1.6])
                    with ec1:
                        st.markdown(f"**{p.get('order_no', '')}**")
                        st.caption(f"用户：`{u_email}` | 下单于 {created}")
                    with ec2:
                        st.markdown(f"**{plan_d}** · ¥{p.get('amount', 0)}")
                        st.caption(f"时长：{p.get('months', 1)} 个月")
                    with ec3:
                        if st.button("↩️ 恢复为待确认", key=f"restore_{p.get('id')}",
                                     use_container_width=True):
                            ok_r, msg_r = database.restore_payment_to_pending(p["id"])
                            st.session_state["admin_flash"] = (
                                "ok" if ok_r else "err", msg_r)
                            st.rerun()

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
                user_obj = None
                _lookup_err = ""
                try:
                    user_obj = database.get_user_by_email(target_email)
                except RuntimeError as _e:
                    _lookup_err = str(_e)
                if _lookup_err:
                    st.error(f"数据库暂时连不上（网络波动），请稍后重试。{_lookup_err}")
                elif not user_obj:
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

# ================= Tab 4: 密码重置 =================
# 为什么必须有人工入口：自助找回依赖「能把消息送到用户手上」——已绑微信或
# 邮箱能收信。而现实里这两条都可能断（没绑、进垃圾箱、邮箱早就不用了），
# 用户此时唯一能做的就是找站长。没有这个入口，他就被永久锁在账号外面，
# 而账号里还挂着他付过钱的 VIP。
with tab_reset:
    st.subheader("🔑 密码重置")
    st.caption("为忘记密码、且自助通道（微信推送 / 邮件）没能送达的用户生成重置码。"
               "重置码**只在生成时显示一次**，请通过既有联系渠道告知用户。")

    if not database.check_password_reset_tables():
        st.error("⚠️ 密码重置相关表尚未创建，本页功能不可用。")
        with st.expander("📋 点击查看建表 SQL（复制到 Supabase SQL Editor 执行）", expanded=True):
            sql_path = os.path.join(_ROOT, "init_password_reset.sql")
            if os.path.exists(sql_path):
                with open(sql_path, "r", encoding="utf-8") as f:
                    st.code(f.read(), language="sql")
            else:
                st.warning("未找到 init_password_reset.sql 文件")
        st.stop()

    # 生成结果走 flash：紧跟 st.rerun() 的 st.success 会瞬间消失，
    # 而「码」本身是**只显示一次**的东西——消失就等于丢了，必须让它活过重跑。
    _reset_flash = st.session_state.pop("admin_reset_flash", None)
    if _reset_flash:
        _rkind, _rtext, _rcode = _reset_flash
        (st.success if _rkind == "ok" else st.error)(_rtext)
        if _rcode:
            st.caption(f"重置码（{pr.CODE_TTL_MINUTES} 分钟内有效，用一次即失效）"
                       f"——请立即复制，页面刷新后不再显示：")
            st.code(_rcode, language=None)

    st.markdown("---")
    st.subheader("⏳ 待处理的人工重置申请")
    st.caption("用户自助通道全部失败时会自动落到这里，也会同时推给你的微信/Server酱。")

    _reqs = database.get_pending_password_reset_requests()
    if not _reqs:
        st.info("🎉 暂无待处理申请")
    else:
        for r in _reqs:
            with st.container(border=True):
                rc1, rc2, rc3 = st.columns([4, 3, 2])
                with rc1:
                    st.markdown(f"**{r.get('email', '—')}**")
                    st.caption(f"UID {r.get('user_id')} · 提交于 "
                               f"{str(r.get('created_at', ''))[:19].replace('T', ' ')}")
                with rc2:
                    st.caption(r.get("note") or "（未填写原因）")
                with rc3:
                    if st.button("生成重置码", key=f"reset_gen_{r.get('id')}",
                                 use_container_width=True):
                        ok, code, note = auth.admin_issue_reset_code(r.get("email"))
                        if ok:
                            database.mark_reset_request_handled(r.get("id"))
                            st.session_state["admin_reset_flash"] = (
                                "ok", f"已为 `{r.get('email')}` 生成重置码。{note}", code)
                        else:
                            st.session_state["admin_reset_flash"] = (
                                "err", f"生成失败：{note}", "")
                        st.rerun()
                    if st.button("仅标记已处理", key=f"reset_done_{r.get('id')}",
                                 use_container_width=True):
                        okk = database.mark_reset_request_handled(r.get("id"))
                        st.session_state["admin_reset_flash"] = (
                            ("ok", "已标记为处理完毕。", "") if okk else
                            ("err", "⚠️ 标记未生效：云端没有更新到任何一行。", ""))
                        st.rerun()

    st.markdown("---")
    st.subheader("手动为任意账号生成重置码")
    st.caption("用户直接找上门（微信/邮件）时用这个，不必等他先提交申请。")
    with st.form("admin_gen_reset_form"):
        target_email = st.text_input("用户邮箱", placeholder="输入已注册用户的邮箱")
        go_gen = st.form_submit_button("生成重置码 🔑")
        if go_gen:
            if not target_email:
                st.error("请输入用户邮箱")
            else:
                ok, code, note = auth.admin_issue_reset_code(target_email)
                st.session_state["admin_reset_flash"] = (
                    ("ok", f"已为 `{target_email}` 生成重置码。{note}", code) if ok
                    else ("err", f"生成失败：{note}", ""))
                st.rerun()

# ================= Tab 5: 站点设置 =================
# 为什么需要这一页：店铺入口这类「运营侧决定」如果写死在代码里，每次开关都要
# 改代码 + 提交 + 等 Cloud 重新部署；而关店/开店是当天就要生效的事。
# 存进 site_settings 表后，在这里点一下就行，对访客立即生效，也不需要会写代码。
with tab_site:
    st.subheader("🧩 站点设置")
    st.caption("这里的改动即时生效，不需要改代码、不需要等重新部署。")

    # 走 flash：紧跟 st.rerun() 的 st.success 会立刻消失（同密码重置那段的处理）
    _site_flash = st.session_state.pop("admin_site_flash", None)
    if _site_flash:
        _skind, _stext = _site_flash
        {"ok": st.success, "warn": st.warning, "err": st.error}[_skind](_stext)

    st.markdown("### 🛒 侧边栏店铺入口")

    _cfg = site_settings.read_shop_entry()
    _err = _cfg.get("err") or {}
    # 「表没建」要给出建表 SQL；「网络抖动」要让人重试 —— 两者不能混成一句
    # 「读取失败」，否则管理员会去执行一个根本没用的迁移脚本。
    _table_missing = (_cfg["source"] == "db-read-failed"
                      and (str(_err.get("code")) == "PGRST205"
                           or _err.get("status") == 404))

    if _table_missing:
        st.warning("⚠️ `site_settings` 表尚未创建，这里暂时改不了。"
                   "侧边栏入口当前按代码默认值处理（隐藏），前台不会报错。")
        with st.expander("📋 点击查看建表 SQL（复制到 Supabase SQL Editor 执行）",
                         expanded=True):
            _sql_path = os.path.join(_ROOT, "init_site_settings.sql")
            if os.path.exists(_sql_path):
                with open(_sql_path, "r", encoding="utf-8") as f:
                    st.code(f.read(), language="sql")
            else:
                st.warning("未找到 init_site_settings.sql 文件")
    elif _cfg["source"] == "db-read-failed":
        st.error(f"读取站点设置失败（HTTP {_err.get('status')}）："
                 f"{_err.get('message') or _err.get('detail') or '未知原因'}")
        st.caption("这是**取不到**，不是「没有配置」——前台此时按默认值（隐藏）显示。")
    else:
        if _cfg["source"] == "default":
            st.info("表已就绪但还没有配置记录，当前按代码默认值显示（隐藏）。"
                    "保存一次即写入。")

        # 不给 key：Streamlit 带 key 的 widget 在 rerun 时会用自己存的旧值、
        # 忽略 value=，那样保存后从库里读到的新值刷不进控件（见 tech-pitfalls）。
        _shop_on = st.toggle(
            "在侧边栏显示店铺入口",
            value=bool(_cfg["enabled"]),
            help="关掉后对所有访问者（包括管理员自己）都不可见，页面上不留任何残影。")
        _shop_label = st.text_input(
            "按钮文案", value=_cfg["label"],
            help="例如「🛒 全天候小店」。以后换商品只改这里。")
        _shop_url = st.text_input(
            "按钮链接",
            value=_cfg["url"] or site_settings.SHOP_ENTRY_DEFAULT_URL,
            help="必须以 http:// 或 https:// 开头；其他协议前台会拒绝渲染（防伪协议注入）。")

        _c_save, _c_state = st.columns([1, 3])
        with _c_save:
            _do_save = st.button("💾 保存", type="primary", use_container_width=True)
        with _c_state:
            _src_txt = {"db": "来自站点设置表", "default": "代码默认值"}.get(
                _cfg["source"], _cfg["source"])
            st.caption(f"当前：**{'显示' if _cfg['enabled'] else '隐藏'}** · 来源：{_src_txt}")

        if _do_save:
            _ok, _save_err, _warn = site_settings.save_shop_entry(
                _shop_on, _shop_label, _shop_url,
                updated_by=auth.get_user_email())
            if not _ok:
                st.session_state["admin_site_flash"] = (
                    "err",
                    f"保存失败：{_save_err.get('message') if _save_err else '未知原因'}"
                    "（若提示找不到表，请先执行 init_site_settings.sql）")
            elif _warn:
                # 存进去了，但前台会拒渲染 —— 必须当场说，别等发现入口不显示才来查
                st.session_state["admin_site_flash"] = ("warn", f"已保存，但{_warn}")
            else:
                st.session_state["admin_site_flash"] = (
                    "ok", f"已保存，前台立即生效（现在{'显示' if _shop_on else '隐藏'}）。")
            st.rerun()

    st.markdown("---")
    st.markdown("### 🔐 会话签名密钥")
    st.caption("只报「来源」与「长度」，**不会显示密钥本身**。")
    _jsrc = auth.JWT_SECRET_SOURCE
    if _jsrc.startswith(auth.REJECTED_PREFIX) or _jsrc == "ephemeral-random":
        st.error("当前用的是**进程级随机密钥**：服务每次重启（含每次部署）"
                 "都会让所有已登录用户掉线，需要重新登录。")
        st.markdown(
            "**怎么修**（一次性）：Streamlit Cloud → 你的应用 → Settings → Secrets，"
            "把 `[auth]` 下的 `jwt_secret` 换成一个新的随机值（≥32 字符）后保存，"
            "应用会重启一次，之后就不会再掉登录。")
        st.caption(f"当前来源：`{_jsrc}`")
    else:
        st.success(f"已从 `{_jsrc}` 读到持久化密钥（长度 {len(auth.JWT_SECRET)}），"
                   "重启与重新部署都不会让用户掉登录。")

st.markdown("---")
if st.button("⬅️ 返回主页", use_container_width=False):
    st.switch_page("app.py")

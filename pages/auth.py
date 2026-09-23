"""
登录 / 注册 / 忘记密码 页面

为什么用 `st.radio` 而不是 `st.tabs`（2026-09-23）：
    要支持「登录失败 → 点一下直达忘记密码」和「邮件里的 `?reset=` 链接直接
    落到重置表单」，就必须能**以代码切换当前面板**。而 Streamlit 的 tabs
    没有「选中第 N 个」这个能力——写了 key 也读不到选中态，只能靠人家手点。
    所以这里换成 radio 当分段控件：视觉上差不多，但它是真正可编程的。
    注意铁律：带 key 的 widget 在 rerun 时读自己存的 session_state，
    所以「切到某个面板」= 在 widget 创建**之前**写 st.session_state["auth_mode"]。
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
import password_reset as pr

# 解析结果必须真的是根目录那个 auth。若哪天引导再被改坏，本页会在这里
# 明确报错，而不是静默地把整张登录页当成 auth 模块拿去用。
if os.path.realpath(getattr(auth, "__file__", "") or "") != os.path.realpath(
        os.path.join(_ROOT, "auth.py")):
    raise RuntimeError(
        f"auth 模块解析错误：{getattr(auth, '__file__', None)!r}，"
        f"应为 {os.path.join(_ROOT, 'auth.py')!r}")

st.set_page_config(page_title="会员登录 / 注册 - Chilam Club", page_icon="🔐", layout="centered")

MODE_LOGIN = "🔑 用户登录"
MODE_REGISTER = "📝 新用户注册"
MODE_RESET = "🆘 忘记密码"

# 通道凭据（SMTP / WxPusher）从 st.secrets 桥到环境变量。
# 必须在本页任何「会不会发信」的判断之前调用，否则会出现
# 「明明配了邮箱，页面却说没配」——而原因只是桥没搭上，与配置无关。
auth.bridge_channel_secrets()

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

# ================= 进入本页前的状态整理（必须在任何 widget 之前）=================
# 铁律：带 key 的 widget 一旦被创建，本次运行内再改它的 session_state 会抛错。
# 所以「清空重置码输入框」这类动作一律放在这里，而不是在处理完表单之后。

# 邮件/微信里的 ?reset=<码> 链接：预填到重置表单并直接切到忘记密码面板。
_incoming_code = ""
try:
    _incoming_code = str(st.query_params.get("reset", "") or "").strip()
except Exception:
    # 极老版本 Streamlit 没有 st.query_params。本项只是便利功能，
    # 取不到就退化成「用户手动输入」——不能因为它让整页崩掉。
    _incoming_code = ""
if _incoming_code:
    st.session_state["reset_code_input"] = _incoming_code
    st.session_state["auth_mode"] = MODE_RESET
    st.info("已自动填入邮件里的重置码，请补上注册邮箱与新密码后提交。")
    # 立刻把码从地址栏抹掉：用户截图、转发链接、或共用电脑时，
    # 留在 URL 里的重置码会被一并带出去。
    try:
        del st.query_params["reset"]
    except Exception:
        pass

if st.session_state.pop("_clear_reset_code", False):
    st.session_state["reset_code_input"] = ""

# 提示用 flash 传递：紧跟 st.rerun() 的 st.success 会在重跑时瞬间消失，
# 用户看到的只是「点了没反应」。
_flash = st.session_state.pop("auth_flash", None)
if _flash:
    _kind, _text = _flash
    {"ok": st.success, "warn": st.warning, "err": st.error}.get(_kind, st.info)(_text)

# ================= 面板选择 =================
mode = st.radio("功能选择", [MODE_LOGIN, MODE_REGISTER, MODE_RESET],
                horizontal=True, label_visibility="collapsed", key="auth_mode")

# ================= 1. 登录 =================
if mode == MODE_LOGIN:
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
                    # 按**真实原因**给下一步，而不是一律甩一句「请重试」：
                    # 密码错的去重置、没账号的去注册，这是两条不同的路。
                    if "密码" in str(err):
                        st.caption("忘记密码？点上方「🆘 忘记密码」，用注册邮箱自助重置。")
                    elif "不存在" in str(err):
                        st.caption("还没有账号？点上方「📝 新用户注册」，免费注册即可使用。")

# ================= 2. 注册 =================
elif mode == MODE_REGISTER:
    with st.form("register_form"):
        reg_email = st.text_input("邮箱地址", placeholder="your_email@example.com")
        reg_password = st.text_input(f"设置密码 (至少 {auth.MIN_PASSWORD_LENGTH} 位)",
                                    type="password")
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
    st.caption("💡 注册后建议到「会员中心」绑定一次微信推送——将来万一忘记密码，"
               "用微信自助重置比邮箱更可靠（邮箱常被投进垃圾箱）。")

# ================= 3. 忘记密码 =================
else:
    st.markdown("#### 第 1 步：获取重置码")
    st.caption("重置码会发到你**已绑定的微信**；没绑微信则发到**注册邮箱**。"
               "两者都不可用时自动转为管理员人工处理。")

    with st.form("reset_request_form"):
        r_email = st.text_input("注册邮箱", placeholder="your_email@example.com",
                                key="reset_req_email")
        send_code = st.form_submit_button("发送重置码 📨", use_container_width=True)

        if send_code:
            status, msg = auth.request_password_reset(r_email)
            st.session_state["reset_email"] = (r_email or "").strip().lower()
            if status == "sent":
                # 走 flash：发送成功后要 st.rerun() 让下方表单拿到新的邮箱预填，
                # 而 rerun 会吞掉直接写在表单里的提示。
                st.session_state["auth_flash"] = ("ok", msg)
                st.rerun()
            elif status == "manual":
                st.session_state["auth_flash"] = ("warn", msg)
                st.rerun()
            else:
                st.error(msg)

    st.markdown("---")
    st.markdown("#### 第 2 步：填入重置码并设置新密码")
    st.caption(f"重置码 {pr.CODE_TTL_MINUTES} 分钟内有效，用一次即失效；"
               f"连续输错 {pr.MAX_VERIFY_ATTEMPTS} 次会被作废，需要重新获取。")

    with st.form("reset_apply_form"):
        a_email = st.text_input("注册邮箱", placeholder="your_email@example.com",
                                value=st.session_state.get("reset_email", ""),
                                key="reset_apply_email")
        a_code = st.text_input("重置码", placeholder="10 位字母数字，如 A7K2M9PQ3X",
                               key="reset_code_input")
        a_pw = st.text_input(f"新密码 (至少 {auth.MIN_PASSWORD_LENGTH} 位)", type="password")
        a_pw2 = st.text_input("确认新密码", type="password")
        apply_reset = st.form_submit_button("确认重置 🔐", use_container_width=True)

        if apply_reset:
            ok, msg = auth.reset_password_with_code(a_email, a_code, a_pw, a_pw2)
            if ok:
                # 先登记「下次渲染时清空重置码输入框」，再切面板并 rerun。
                # 顺序不能反：widget 创建后再改它的 session_state 会抛错。
                st.session_state["_clear_reset_code"] = True
                st.session_state["auth_mode"] = MODE_LOGIN
                st.session_state["auth_flash"] = ("ok", msg)
                st.rerun()
            else:
                st.error(msg)

    with st.expander("还是收不到？走人工重置", expanded=False):
        st.caption("自助通道都用不上时（没绑微信、站点未配邮件、或消息进不了收件箱），"
                   "可以登记一条人工重置申请，由管理员生成重置码后联系你。")
        if st.button("提交人工重置申请 🙋", key="reset_manual_btn"):
            target = (st.session_state.get("reset_req_email")
                      or st.session_state.get("reset_email") or "")
            status, msg = auth.request_manual_reset(target, "用户在本页主动申请")
            st.session_state["auth_flash"] = (
                ("warn", msg) if status == "manual" else ("err", msg))
            st.rerun()

st.markdown("---")
if st.button("⬅️ 返回主页", use_container_width=True):
    st.switch_page("app.py")

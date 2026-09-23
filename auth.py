"""
认证模块 - 轻量 Token + stdlib + Supabase
"""
import os
import json
import base64
import hmac
import hashlib
import secrets as _pysecrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from functools import wraps

try:
    import streamlit as st
except ImportError:
    st = None
import database

# ==================== JWT 签名密钥 ====================
#
# 这里**绝不能**放可用的默认密钥。原实现写的是
#     JWT_SECRET = os.environ.get("JWT_SECRET", "<一个固定常量>")
# 而本仓库是 **public** —— 那个常量对所有人可见，而它就是 HS256 的签名密钥。
# 后果不是「泄露一点信息」，而是**任何人都能离线自签一个
# {"user_id":1,"is_admin":true} 的 token 直接拿到后台管理权**（确认收款、
# 手动开通 VIP、看全部用户）。这类洞的特征是：功能完全正常、日志毫无异常，
# 所以只能靠「代码里不许有可用默认值」这条规则挡住，不能靠事后发现。
#
# 现在的规则：env / st.secrets 都没配 → 生成**进程级随机密钥**并显式告警。
# 代价只有「进程重启后旧 token 失效」，而 token 本来只存在 st.session_state
# （全仓无 cookie、无 URL 回填 —— 已 grep 确认），刷新页面就已经丢了，
# 所以这个代价用户其实感知不到。**宁可让人重新登录，也不要给一把公开的钥匙。**
#
# 下面这串不是密钥，是**已泄露旧常量的 sha256 前 12 位**：用来在有人把那个
# 旧值填进 Secrets 时**拒绝使用它**（而不只是告警 —— 见 _load_jwt_secret）。
# 指纹不可逆，写在公开仓库无损失；而旧常量本身已在 git 历史里公开，藏也没意义。
_LEAKED_SECRET_FP = "e4af8b5b44d4"


def _fingerprint(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


REJECTED_PREFIX = "ephemeral-random(rejected-leaked"


def _load_jwt_secret() -> tuple[str, str]:
    """返回 (密钥, 来源)。来源用于启动告警，绝不打印密钥本身。"""
    val = os.environ.get("JWT_SECRET", "")
    src = "env:JWT_SECRET"
    if not val:
        try:
            if hasattr(st, "secrets") and "auth" in st.secrets:
                val = st.secrets["auth"].get("jwt_secret", "") or ""
                src = "st.secrets[auth].jwt_secret"
        except Exception:
            val = ""
    if not val:
        # 进程级随机：够强、不落盘、不可预测。
        return _pysecrets.token_urlsafe(48), "ephemeral-random"
    # 「配了」不等于「能用」：如果配的正是那个已在公开仓库里躺过的旧常量，
    # 它对全网都是已知值，等价于**没有密钥**。此时必须**拒用**，
    # 而不是打一行告警然后继续拿它签名 ——
    # 告警只会进日志，洞照样开着；何况本仓库真实情况就是 Secrets 里填的
    # 就是那个旧常量（指纹 e4af8b5b44d4），"仅告警"等于什么都没做。
    # 拒用的代价仅是「重启后需重新登录」，堵住的是「全网可自签管理员」。
    if _fingerprint(val) == _LEAKED_SECRET_FP:
        return _pysecrets.token_urlsafe(48), f"{REJECTED_PREFIX} from {src})"
    return val, src


JWT_SECRET, JWT_SECRET_SOURCE = _load_jwt_secret()

if JWT_SECRET_SOURCE.startswith(REJECTED_PREFIX):
    print("[Auth] ⚠️ 配置里的 JWT_SECRET 正是**曾硬编码进公开仓库的旧常量** —— "
          "它对全网都是已知值，谁都能用它自签管理员 token。"
          f"已**拒绝使用**并改用进程级随机密钥（来源：{JWT_SECRET_SOURCE}）。"
          "站点功能正常，但服务每次重启都要重新登录；"
          "**请立刻在 Secrets 里换成新的高强度随机值**以恢复正常会话。")
elif JWT_SECRET_SOURCE == "ephemeral-random":
    print("[Auth] 未配置 JWT_SECRET（env 或 st.secrets[auth].jwt_secret），"
          "已启用进程级随机密钥：功能正常，但服务重启后需要重新登录。"
          "生产环境请在 Secrets 里配一个高强度随机值。")
elif len(JWT_SECRET) < 32:
    print(f"[Auth] JWT_SECRET 长度仅 {len(JWT_SECRET)}，偏短，建议 ≥32 位随机字符。")

JWT_EXPIRATION_HOURS = 24 * 7  # 7 天有效期

# 订阅套餐配置
VIP_PLANS = {
    "monthly": {"name": "月度 VIP", "months": 1, "price_cny": 25, "price_usd": 25},
    "quarterly": {"name": "季度 VIP", "months": 3, "price_cny": 60, "price_usd": 60},
    "yearly": {"name": "年度 VIP", "months": 12, "price_cny": 200, "price_usd": 200},
}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

def _b64url_decode(s: str) -> bytes:
    padding = '=' * (4 - (len(s) % 4)) if len(s) % 4 != 0 else ''
    return base64.urlsafe_b64decode(s + padding)

def create_jwt_token(user_id: int, email: str, is_admin: bool = False) -> str:
    """创建 HS256 JWT Token (stdlib 实现，零第三方库依赖)"""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "user_id": user_id,
        "email": email,
        "is_admin": is_admin,
        "exp": int((datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)).timestamp()),
        "iat": int(datetime.utcnow().timestamp())
    }
    
    h_bytes = _b64url_encode(json.dumps(header).encode('utf-8'))
    p_bytes = _b64url_encode(json.dumps(payload).encode('utf-8'))
    msg = f"{h_bytes}.{p_bytes}"
    sig = hmac.new(JWT_SECRET.encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).digest()
    s_bytes = _b64url_encode(sig)
    return f"{msg}.{s_bytes}"


def decode_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """解码并验证 JWT Token"""
    if not token or not isinstance(token, str):
        return None
    parts = token.split('.')
    if len(parts) != 3:
        return None
    h_b64, p_b64, s_b64 = parts
    msg = f"{h_b64}.{p_b64}"
    expected_sig = hmac.new(JWT_SECRET.encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).digest()
    actual_sig = _b64url_decode(s_b64)
    
    if not hmac.compare_digest(expected_sig, actual_sig):
        return None
        
    try:
        payload = json.loads(_b64url_decode(p_b64).decode('utf-8'))
        if "exp" in payload and datetime.utcnow().timestamp() > payload["exp"]:
            return None
        return payload
    except Exception:
        return None


def get_current_user() -> Optional[Dict[str, Any]]:
    """从 session_state 获取当前登录用户"""
    token = st.session_state.get("token")
    if not token:
        return None
    return decode_jwt_token(token)


def is_logged_in() -> bool:
    """检查是否已登录"""
    return get_current_user() is not None


def is_admin() -> bool:
    """检查是否为管理员"""
    user = get_current_user()
    return user is not None and user.get("is_admin", False)


def is_vip() -> bool:
    """检查是否有有效 VIP (管理员默认具备 VIP 权限)"""
    user = get_current_user()
    if not user:
        return False
    if user.get("is_admin", False):
        return True
    return database.is_vip_active(user["user_id"])


def get_user_id() -> Optional[int]:
    """获取当前用户 ID"""
    user = get_current_user()
    return user["user_id"] if user else None


def get_user_email() -> Optional[str]:
    """获取当前用户邮箱"""
    user = get_current_user()
    return user["email"] if user else None


def login(email: str, password: str) -> tuple[bool, str]:
    """用户登录"""
    user = database.get_user_by_email(email)
    if not user:
        return False, "用户不存在"

    if not database.verify_password(user, password):
        return False, "密码错误"

    user_id = user["id"]
    email_val = user["email"]
    is_admin_val = user.get("is_admin", False)

    # 生成 token
    token = create_jwt_token(user_id, email_val, is_admin_val)
    st.session_state["token"] = token
    st.session_state["user_id"] = user_id
    st.session_state["user_email"] = email_val
    st.session_state["is_admin"] = is_admin_val

    return True, ""


def register(email: str, password: str) -> tuple[bool, str]:
    """用户注册"""
    email_clean = email.strip().lower()
    if not email_clean or "@" not in email_clean:
        return False, "请输入有效的邮箱地址"

    if len(password) < 6:
        return False, "密码长度至少为 6 位"

    existing = database.get_user_by_email(email_clean)
    if existing:
        return False, "该邮箱已被注册"

    try:
        user = database.create_user(email_clean, password)
        if not user:
            return False, "创建用户失败，请稍后重试"
        
        token = create_jwt_token(user["id"], user["email"], user.get("is_admin", False))
        st.session_state["token"] = token
        st.session_state["user_id"] = user["id"]
        st.session_state["user_email"] = user["email"]
        st.session_state["is_admin"] = user.get("is_admin", False)
        return True, ""
    except Exception as e:
        return False, f"注册失败: {str(e)}"


def logout():
    """用户登出"""
    for key in ["token", "user_id", "user_email", "is_admin"]:
        if key in st.session_state:
            del st.session_state[key]


def get_plan_display_name(plan_name: str) -> str:
    """获取套餐显示名称"""
    plan = VIP_PLANS.get(plan_name, {})
    return plan.get("name", plan_name)


def get_vip_remaining_days() -> Optional[int]:
    """当前登录用户 VIP 剩余天数 (None=无有效VIP)"""
    user = get_current_user()
    if not user:
        return None
    if user.get("is_admin", False):
        return 999
    return database.get_vip_remaining_days(user["user_id"])


def get_vip_status() -> Dict[str, Any]:
    """当前登录用户 VIP 完整状态"""
    user = get_current_user()
    if not user:
        return {"is_active": False, "plan_name": None, "expires_at": None, "remaining_days": None, "is_admin": False}
    if user.get("is_admin", False):
        return {"is_active": True, "plan_name": "admin", "expires_at": None, "remaining_days": 999, "is_admin": True}
    return database.get_vip_status_detail(user["user_id"])


def get_plan_info(plan_key: str) -> Optional[Dict[str, Any]]:
    """获取套餐配置信息"""
    return VIP_PLANS.get(plan_key)


def get_all_plans() -> Dict[str, Any]:
    """获取全部套餐配置"""
    return VIP_PLANS


# ==================== 密码修改 与 忘记密码 ====================
#
# 这一节是**唯一**把「谁有资格改这个账号的口令」写成代码的地方。两条路径的
# 信任前提完全不同，改动前务必分清，不要把其中一条的判断顺手用到另一条上：
#
#   change_password              —— 已登录。靠「知道当前密码」证明身份。
#                                   所以它必须真的拿当前密码去 verify，
#                                   而不能因为「token 有效」就放行：token 可能是
#                                   在别人没锁屏的机器上捡到的，而改密码是
#                                   **夺取账号**的动作，门槛必须比「看一眼数据」高。
#
#   reset_password_with_code     —— 未登录。靠「能收到发往该账号的消息」证明身份。
#                                   所以码必须一次性、短效、限次，且**邮箱与码要同时
#                                   对得上**（另一重弱因子，见 password_reset.reset_link
#                                   的注释：链接里刻意不带邮箱）。
#
# 两条路径的公共红线：
#   · 新口令一律走 database.hash_password（PBKDF2 + 随机盐），绝不自己拼哈希；
#   · 改密/重置成功后，作废该账号所有未消费的重置码；
#   · 提示文案里**永远不出现口令或重置码**。

MIN_PASSWORD_LENGTH = 6

# 需要从 st.secrets 桥到环境变量的通道凭据。
# mailer / wxpusher / admin_notify 都不 import streamlit（它们要在
# GitHub Actions 里跑），只认环境变量；而站内运行时凭据在 st.secrets。
_CHANNEL_KEYS = (
    "DIGEST_SMTP_HOST", "DIGEST_SMTP_PORT", "DIGEST_SMTP_USER",
    "DIGEST_SMTP_PASS", "DIGEST_SMTP_FROM",
    "WXPUSHER_APP_TOKEN", "DIGEST_SERVERCHAN_KEY",
    "SITE_URL",
)

# 允许「收在子表里」的写法（有人习惯 [smtp] host=… 而不是一堆平铺键）。
# 用显式映射而不是按名字猜：猜出来的键名一旦对不上，表现是「配了却不生效」。
_NESTED_KEYS = {
    "WXPUSHER_APP_TOKEN": ("wxpusher", "app_token"),
    "DIGEST_SMTP_HOST": ("smtp", "host"),
    "DIGEST_SMTP_PORT": ("smtp", "port"),
    "DIGEST_SMTP_USER": ("smtp", "user"),
    "DIGEST_SMTP_PASS": ("smtp", "pass"),
    "DIGEST_SMTP_FROM": ("smtp", "from"),
}


def bridge_channel_secrets() -> None:
    """把通道凭据从 st.secrets 桥到环境变量。**全站唯一实现**。

    已存在则不覆盖：在 GitHub Actions 里环境变量才是唯一来源，
    Secrets 若也塞了同名键，不该反过来盖掉运行环境的值。
    """
    if st is None:
        return
    for name in _CHANNEL_KEYS:
        if os.environ.get(name):
            continue
        val = ""
        try:
            val = str(st.secrets.get(name, "") or "").strip()
        except Exception:
            val = ""
        if not val and name in _NESTED_KEYS:
            sec, key = _NESTED_KEYS[name]
            try:
                if sec in st.secrets:
                    val = str((st.secrets[sec] or {}).get(key, "") or "").strip()
            except Exception:
                val = ""
        if val:
            os.environ[name] = val


def validate_new_password(password: str, confirm: str) -> Optional[str]:
    """校验新口令。返回错误文案；None 表示通过。"""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"新密码长度至少 {MIN_PASSWORD_LENGTH} 位"
    if password != confirm:
        return "两次输入的新密码不一致"
    return None


def change_password(current_password: str, new_password: str,
                    confirm: str) -> tuple[bool, str]:
    """已登录用户修改密码。返回 (成功, 说明)。

    为什么必须重新拉一次用户行，而不是直接用 token 里的信息：
    token 里只有 user_id/email/is_admin，**没有 password_hash**，
    没法比对当前密码。而「不验当前密码就允许改密码」等价于
    「任何拿到有效 token 的人都能夺号」——本文件开头已说明这个门槛
    必须高于普通操作。
    """
    user = get_current_user()
    if not user:
        return False, "登录状态已失效，请重新登录后再试"
    email_val = user.get("email") or ""
    row = database.get_user_by_email(email_val) if email_val else None
    if not row:
        # 有 token 但库里读不到账号：这**不是**「密码错误」。
        # 混为一谈会让用户反复重输旧密码，而真实原因在账号/取数侧。
        return False, "读取账号信息失败（可能是云端取数异常），请稍后重试；持续失败请联系管理员"

    if not database.verify_password(row, current_password or ""):
        return False, "当前密码不正确"

    err = validate_new_password(new_password, confirm)
    if err:
        return False, err
    if database.verify_password(row, new_password or ""):
        return False, "新密码不能与当前密码相同"

    ok, werr = database.update_user_password(row["id"], new_password)
    if not ok:
        detail = (werr or {}).get("message") or "未知原因"
        return False, f"密码写入失败：{detail}"

    # 改完顺手作废所有未消费的重置码：否则「我改过密码了」和
    # 「之前发出去的那枚码还有效」可以同时成立——用户会认为改密码没生效。
    database.invalidate_reset_codes(row["id"])
    return True, "密码已修改。请用新密码登录（本机当前会话保持有效）。"


def _code_expired(expires_at) -> bool:
    """过期判定。**解析失败按已过期处理（fail closed）**：
    宁可让用户重新取一枚码，也不要因为解析异常把一枚状态未知的码放行。"""
    s = str(expires_at or "").strip()
    if not s:
        return True
    try:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return True


def _notify_admins_reset_request(email_val: str, user_id: int, reason: str) -> tuple[bool, str]:
    """把人工重置申请推给管理员。返回 (是否送达, 说明)。"""
    try:
        import admin_notify
    except Exception as e:
        return False, f"告警模块不可用：{type(e).__name__}"
    body = (
        "## 有用户需要人工重置密码\n\n"
        f"- 账号：`{email_val}`（user_id={user_id}）\n"
        f"- 原因：{reason}\n\n"
        "自助通道（微信推送 / 邮件）没能送达。请到「后台管理 → 密码重置」"
        "为该账号生成一枚重置码，通过你与用户既有的联系渠道告知对方。"
    )
    try:
        return admin_notify.notify_admins("🔑 密码重置申请 · 需人工处理", body)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def request_manual_reset(email: str, reason: str = "用户主动申请") -> tuple[str, str]:
    """记一条人工重置申请并通知管理员。返回 (status, 文案)，status 恒为 'manual'。

    返回三态字符串（而不是 bool）是为了让页面能选对颜色：
    「已转人工」既不是成功（码没发出去）也不是失败（申请确实记下了），
    用 bool 会把这种情况逼成二者之一，提示语气必然错一半。
    """
    bridge_channel_secrets()
    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return "failed", "请输入有效的邮箱地址"
    row = database.get_user_by_email(email_clean)
    if not row:
        return "failed", "该邮箱尚未注册。请检查是否输错，或先注册一个新账号。"

    _, derr = database.create_password_reset_request(row["id"], note=reason[:200])
    if derr:
        return "failed", (f"申请登记失败：{derr.get('message') or '未知原因'}。"
                          "请通过站点页脚的联系方式直接联系管理员。")

    delivered, note = _notify_admins_reset_request(email_clean, row["id"], reason)
    if delivered:
        return "manual", ("已将人工重置申请提交给管理员，并已通知到位。\n\n"
                          "管理员会通过你注册时留下的联系方式与你确认，请留意。")
    # 申请已落库但没通知到——必须说清「下一步要你自己去找管理员」，
    # 否则用户会安心地等一个没人知道的申请。
    return "manual", ("人工重置申请已登记，但**自动通知管理员失败了**"
                      f"（{note}）。\n\n请主动通过站点页脚的联系方式联系管理员，"
                      "否则没人会看到这条申请。")


def request_password_reset(email: str) -> tuple[str, str]:
    """发起「忘记密码」。返回 (status, 文案)，status ∈ sent / manual / failed。

    通道选择顺序：**已绑微信 → 微信推送；否则 → 邮件（若已配置）**。
    两条都试，先成功者算送达；全失败则转人工。顺序依据是到达率：
    微信推送是用户自己扫码绑的，比「注册邮箱是否还在收信」可靠得多 ——
    而「去垃圾邮件箱翻一翻」正是这类流程最常见的失败点。
    """
    bridge_channel_secrets()
    import password_reset as pr
    import mailer

    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return "failed", "请输入有效的邮箱地址"

    row = database.get_user_by_email(email_clean)
    if not row:
        # 口径与登录/注册保持一致：如实告知。注册接口本来就会回
        # 「该邮箱已被注册」，邮箱是否存在并非秘密；在这里假装中立，
        # 只会让打错一个字符的用户永远收不到码、还以为是系统坏了。
        return "failed", "该邮箱尚未注册。请检查是否输错，或先注册一个新账号。"

    uid = row["id"]

    # ---- 限流：先查计数，再决定要不要生成码 ----
    # count 返回 -1 表示取数失败。此时**放行**并如实提示限流未生效：
    # 云端抖一下就锁住所有人的密码找回，代价远大于少拦一次。
    per_min = database.count_reset_requests(uid, 1)
    per_hour = database.count_reset_requests(uid, 60)
    limit_note = ""
    if per_min == -1 or per_hour == -1:
        limit_note = "（当前无法核对请求频率，频率限制本次未生效）"
    elif per_min >= 1:
        return "failed", f"请求过于频繁，请 {pr.RESEND_COOLDOWN_SECONDS} 秒后再试。"
    elif per_hour >= pr.MAX_REQUESTS_PER_HOUR:
        return "failed", (f"一小时内最多请求 {pr.MAX_REQUESTS_PER_HOUR} 次，"
                          "已达上限。请稍后再试，或联系管理员人工重置。")

    wx_uid = database.get_user_wxpusher_uid(uid)
    email_ready = mailer.is_configured()

    if not wx_uid and not email_ready:
        return request_manual_reset(
            email_clean, "站点未配置可用的自助投递通道（无微信绑定且未配 SMTP）")

    code = pr.generate_code()
    salt = pr.new_salt()

    # 发新码前作废旧的：让「一个账号同时存在多枚有效码」不可能出现。
    database.invalidate_reset_codes(uid)

    # **先落库再投递**：投递失败也要计入限流，否则一个坏通道会被无限重试，
    # 把发信配额和推送额度打光。未投递的码本身无法被任何人使用，留着无害。
    rec, derr = database.create_password_reset_code(
        uid, "wxpusher" if wx_uid else "email", salt,
        pr.hash_code(code, salt), pr.CODE_TTL_MINUTES)
    if derr:
        return "failed", (f"重置码写入失败：{derr.get('message') or '未知原因'}。"
                          "请稍后重试或联系管理员。")
    code_id = rec.get("id")

    plan = []
    if wx_uid:
        plan.append(("wxpusher", lambda: pr.deliver_wxpusher(wx_uid, code)))
    if email_ready:
        plan.append(("email", lambda: pr.deliver_email(email_clean, code)))

    failures = []
    for chan, send in plan:
        try:
            ok, note = send()
        except Exception as e:
            ok, note = False, f"{type(e).__name__}"
        if ok:
            database.update_reset_code_delivery(code_id, True, f"{chan}: {note}")
            masked = pr.masked_email(email_clean)
            where = "你的微信" if chan == "wxpusher" else masked
            return "sent", (
                f"✅ 重置码已发送到 **{where}**。\n\n"
                f"请在 {pr.CODE_TTL_MINUTES} 分钟内，把收到的码连同你的注册邮箱"
                "一起填入下方表单完成重置。" + (f"\n\n{limit_note}" if limit_note else ""))
        failures.append(f"{chan}: {note}")

    database.update_reset_code_delivery(code_id, False, "；".join(failures))
    # 自助通道全灭 → 自动转人工，并把失败原因如实带出来（不要把「发不出去」
    # 说成「已发送」，那样用户会一直去翻收件箱）。
    status, msg = request_manual_reset(
        email_clean, "自助通道投递失败（" + "；".join(failures)[:120] + "）")
    return status, "⚠️ 自动发送未能送达（" + "；".join(failures) + "）。\n\n" + msg


def reset_password_with_code(email: str, code: str, new_password: str,
                             confirm: str) -> tuple[bool, str]:
    """用重置码设置新密码（未登录路径）。返回 (成功, 说明)。"""
    bridge_channel_secrets()
    import password_reset as pr

    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return False, "请输入注册时使用的邮箱地址"
    if not pr.normalize_code(code):
        return False, "请输入收到的重置码"

    row = database.get_user_by_email(email_clean)
    if not row:
        # 仍如实告知：码本身已经验证过归属，这里说「邮箱不对」是用户能
        # 立刻纠正的信息，含糊其辞只会让他反复重试同一组输入。
        return False, "该邮箱尚未注册。请确认你填的是注册时用的邮箱。"

    err = validate_new_password(new_password, confirm)
    if err:
        return False, err

    rec = database.get_active_reset_code(row["id"])
    if not rec:
        return False, "没有可用的重置码（可能已使用、已过期，或已被新的一次请求作废）。请重新获取。"

    if _code_expired(rec.get("expires_at")):
        database.consume_reset_code(rec["id"])
        return False, f"重置码已过期（有效期 {pr.CODE_TTL_MINUTES} 分钟），请重新获取。"

    attempts = int(rec.get("attempts") or 0)
    if attempts >= pr.MAX_VERIFY_ATTEMPTS:
        database.consume_reset_code(rec["id"])
        return False, (f"该重置码的试错次数已达上限（{pr.MAX_VERIFY_ATTEMPTS} 次）并已作废，"
                       "请重新获取一枚。")

    if not pr.verify_code(code, rec.get("code_salt"), rec.get("code_hash")):
        used = attempts + 1
        database.bump_reset_attempts(rec["id"], used)
        left = pr.MAX_VERIFY_ATTEMPTS - used
        if left <= 0:
            # 用完最后一次机会就地作废，避免「已知已错的码」继续挂着
            # 占用「该用户最近一条有效码」的位置。
            database.consume_reset_code(rec["id"])
            return False, (f"重置码不正确，且已用完 {pr.MAX_VERIFY_ATTEMPTS} 次尝试机会。"
                           "该码已作废，请重新获取。")
        return False, f"重置码不正确（还可尝试 {left} 次）。请核对后重新输入。"

    ok, werr = database.update_user_password(row["id"], new_password)
    if not ok:
        detail = (werr or {}).get("message") or "未知原因"
        return False, f"密码写入失败：{detail}。请稍后重试或联系管理员。"

    database.consume_reset_code(rec["id"])
    database.invalidate_reset_codes(row["id"])
    return True, "✅ 密码已重置成功，请用新密码登录。"


def admin_issue_reset_code(email: str) -> tuple[bool, str, str]:
    """管理员为指定账号生成一枚重置码（线下告知用户）。

    返回 (是否成功, 明文码, 说明)。明文码**只出现在这个返回值里**：
    不落库、不写日志、不进告警消息 —— 它的传递路径是「管理员当面/微信告诉用户」，
    多一个副本就多一条泄露路径。
    """
    import password_reset as pr

    row = database.get_user_by_email((email or "").strip().lower())
    if not row:
        return False, "", "未找到该邮箱对应的账号"

    code = pr.generate_code()
    salt = pr.new_salt()
    database.invalidate_reset_codes(row["id"])
    rec, derr = database.create_password_reset_code(
        row["id"], "admin", salt, pr.hash_code(code, salt),
        pr.CODE_TTL_MINUTES, note="管理员线下发放")
    if derr:
        return False, "", f"写入失败：{derr.get('message') or '未知原因'}"
    database.update_reset_code_delivery(rec.get("id"), True, "管理员线下发放")
    return True, code, (f"已生成，{pr.CODE_TTL_MINUTES} 分钟内有效，"
                        f"最多试错 {pr.MAX_VERIFY_ATTEMPTS} 次、用一次即失效")

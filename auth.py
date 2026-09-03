"""
认证模块 - 轻量 Token + stdlib + Supabase
"""
import os
import json
import base64
import hmac
import hashlib
import secrets as _pysecrets
from datetime import datetime, timedelta
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

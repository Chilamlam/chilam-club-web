"""
Supabase REST 数据访问层 (纯标准库 urllib + hashlib 实现，零额外网络依赖，极速轻量)
替代 SQLite 实现持久化存储，适配 Streamlit Cloud
"""
import os
import json
import urllib.request
import urllib.parse
import urllib.error
import hashlib
import hmac
import base64
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
try:
    import streamlit as st
except ImportError:
    st = None

def _get_config():
    """获取 Supabase 配置 (优先 Streamlit Secrets，其次环境变量)"""
    url = ""
    key = ""
    
    try:
        if hasattr(st, "secrets") and "supabase" in st.secrets:
            url = st.secrets["supabase"].get("url", "")
            key = st.secrets["supabase"].get("service_key") or st.secrets["supabase"].get("key", "")
    except Exception:
        pass

    if not url:
        url = os.environ.get("SUPABASE_URL", "")
    if not key:
        key = os.environ.get("SUPABASE_KEY", "")
    
    return url.rstrip("/"), key

def _supabase_request(method: str, endpoint: str, params: dict = None, json_data: Any = None, headers_extra: dict = None) -> Any:
    """封装 Supabase PostgREST 请求"""
    base_url, api_key = _get_config()
    query_str = ""
    if params:
        query_str = "?" + "&".join([f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()])
        
    url = f"{base_url}/rest/v1/{endpoint.lstrip('/')}{query_str}"
    
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    if headers_extra:
        headers.update(headers_extra)
        
    data_bytes = None
    if json_data is not None:
        data_bytes = json.dumps(json_data).encode("utf-8")
        
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            if body:
                return json.loads(body)
            return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"[Supabase HTTP Error] {method} {url} -> {e.code}: {err_body}")
        return None
    except Exception as e:
        print(f"[Supabase Connection Error] {e}")
        return None

# ==================== 密码哈希 (PBKDF2-HMAC-SHA256 stdlib) ====================

def hash_password(password: str) -> str:
    """生成带 salt 的 pbkdf2 哈希字符串"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"pbkdf2_sha256$100000${salt.hex()}${dk.hex()}"

def verify_password(user: Dict[str, Any], password: str) -> bool:
    """验证用户密码"""
    if not user or "password_hash" not in user:
        return False
    pw_hash = user["password_hash"]
    
    # 兼容 pbkdf2_sha256
    if pw_hash.startswith("pbkdf2_sha256$"):
        parts = pw_hash.split("$")
        if len(parts) != 4:
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        target_dk = parts[3]
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk.hex(), target_dk)
    
    # 兼容初始默认账号明文/老哈希
    if pw_hash.startswith("$2b$") or pw_hash.startswith("$2a$"):
        try:
            import bcrypt
            return bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8"))
        except ImportError:
            # 如果没装 bcrypt 且是初始管理员 chilam666
            if user.get("email") == "chilam@admin.com" and password == "chilam666":
                return True
            return False

    return False


# ==================== 用户 CRUD ====================

def create_user(email: str, password: str, is_admin: bool = False) -> Optional[Dict[str, Any]]:
    """创建新用户"""
    password_hash = hash_password(password)
    data = {
        "email": email.strip().lower(),
        "password_hash": password_hash,
        "is_admin": is_admin
    }
    res = _supabase_request("POST", "users", json_data=data)
    if res and isinstance(res, list) and len(res) > 0:
        return res[0]
    return None

def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """通过邮箱查询用户"""
    params = {"email": f"eq.{email.strip().lower()}", "select": "*"}
    res = _supabase_request("GET", "users", params=params)
    if res and isinstance(res, list) and len(res) > 0:
        return res[0]
    return None

def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """通过 ID 获取用户"""
    params = {"id": f"eq.{user_id}", "select": "*"}
    res = _supabase_request("GET", "users", params=params)
    if res and isinstance(res, list) and len(res) > 0:
        return res[0]
    return None

def get_all_users() -> List[Dict[str, Any]]:
    """获取所有用户列表"""
    params = {"select": "*", "order": "created_at.desc"}
    res = _supabase_request("GET", "users", params=params)
    return res if isinstance(res, list) else []


# ==================== 订阅 CRUD ====================

def create_subscription(user_id: int, plan_name: str, months: int) -> Optional[Dict[str, Any]]:
    """创建/更新 VIP 订阅"""
    now = datetime.utcnow()
    expires_at = now + timedelta(days=30 * months)
    
    data = {
        "user_id": user_id,
        "plan_name": plan_name,
        "status": "active",
        "start_at": now.isoformat() + "Z",
        "expires_at": expires_at.isoformat() + "Z"
    }
    res = _supabase_request("POST", "subscriptions", json_data=data)
    if res and isinstance(res, list) and len(res) > 0:
        return res[0]
    return None

def get_active_subscription(user_id: int) -> Optional[Dict[str, Any]]:
    """获取用户当前有效 VIP 订阅"""
    now_iso = datetime.utcnow().isoformat() + "Z"
    params = {
        "user_id": f"eq.{user_id}",
        "status": "eq.active",
        "expires_at": f"gt.{now_iso}",
        "order": "expires_at.desc",
        "limit": "1"
    }
    res = _supabase_request("GET", "subscriptions", params=params)
    if res and isinstance(res, list) and len(res) > 0:
        return res[0]
    return None

def get_user_subscriptions(user_id: int) -> List[Dict[str, Any]]:
    """获取指定用户所有订阅记录"""
    params = {
        "user_id": f"eq.{user_id}",
        "order": "created_at.desc"
    }
    res = _supabase_request("GET", "subscriptions", params=params)
    return res if isinstance(res, list) else []

def get_all_subscriptions() -> List[Dict[str, Any]]:
    """获取全站所有订阅记录（后台管理）"""
    params = {
        "select": "*",
        "order": "created_at.desc"
    }
    res = _supabase_request("GET", "subscriptions", params=params)
    return res if isinstance(res, list) else []

def is_vip_active(user_id: int) -> bool:
    """检查是否处于有效 VIP"""
    sub = get_active_subscription(user_id)
    return sub is not None

def manual_create_subscription(user_id: int, plan_name: str, months: int) -> Optional[Dict[str, Any]]:
    """管理员手动开通 VIP"""
    return create_subscription(user_id, plan_name, months)


# ==================== 用户自选股 Watchlist CRUD ====================

def get_user_watchlist(user_id: int) -> List[str]:
    """获取用户自选股代码列表 (从 Supabase users.watchlist 或独立表读取)"""
    user = get_user_by_id(user_id)
    if user and "watchlist" in user and user["watchlist"]:
        if isinstance(user["watchlist"], list):
            return user["watchlist"]
        if isinstance(user["watchlist"], str):
            try:
                return json.loads(user["watchlist"])
            except Exception:
                return [c.strip() for c in user["watchlist"].split(",") if c.strip()]
    return []

def update_user_watchlist(user_id: int, watchlist: List[str]) -> bool:
    """更新保存用户自选股"""
    # 查重并规范化
    cleaned = list(dict.fromkeys([c.strip().upper() for c in watchlist if c.strip()]))
    
    # 尝试更新 users 表
    res = _supabase_request("PATCH", f"users?id=eq.{user_id}", json_data={"watchlist": cleaned})
    if res is not None:
        return True
    return False


def initialize():
    """兼容旧接口"""
    pass


# ==================== 订单 Payments CRUD ====================

def generate_order_no() -> str:
    """生成唯一订单号: CC + 年月日时分 + 6位随机"""
    now = datetime.utcnow()
    rand = os.urandom(3).hex().upper()
    return f"CC{now.strftime('%Y%m%d%H%M')}{rand}"

def create_payment(user_id: int, plan_name: str, months: int, amount: float,
                   payment_method: str = "wechat") -> Optional[Dict[str, Any]]:
    """创建待支付订单 (status=pending)"""
    order_no = generate_order_no()
    data = {
        "user_id": user_id,
        "order_no": order_no,
        "plan_name": plan_name,
        "months": months,
        "amount": amount,
        "currency": "CNY",
        "payment_method": payment_method,
        "status": "pending"
    }
    res = _supabase_request("POST", "payments", json_data=data)
    if res and isinstance(res, list) and len(res) > 0:
        return res[0]
    return None

def get_payment_by_id(payment_id: int) -> Optional[Dict[str, Any]]:
    """按 ID 获取订单"""
    params = {"id": f"eq.{payment_id}", "select": "*"}
    res = _supabase_request("GET", "payments", params=params)
    if res and isinstance(res, list) and len(res) > 0:
        return res[0]
    return None

def get_pending_payments() -> List[Dict[str, Any]]:
    """获取所有待处理订单 (管理员用)"""
    params = {
        "status": "eq.pending",
        "order": "created_at.asc",
        "select": "*"
    }
    res = _supabase_request("GET", "payments", params=params)
    return res if isinstance(res, list) else []

def get_user_payments(user_id: int) -> List[Dict[str, Any]]:
    """获取用户订单历史"""
    params = {
        "user_id": f"eq.{user_id}",
        "order": "created_at.desc",
        "select": "*"
    }
    res = _supabase_request("GET", "payments", params=params)
    return res if isinstance(res, list) else []

def get_user_pending_payments(user_id: int) -> List[Dict[str, Any]]:
    """获取用户待处理订单"""
    params = {
        "user_id": f"eq.{user_id}",
        "status": "eq.pending",
        "order": "created_at.desc",
        "select": "*"
    }
    res = _supabase_request("GET", "payments", params=params)
    return res if isinstance(res, list) else []

def cancel_payment(payment_id: int) -> bool:
    """取消订单"""
    res = _supabase_request("PATCH", f"payments?id=eq.{payment_id}",
                            json_data={"status": "cancelled"})
    return res is not None


def confirm_payment(payment_id: int, admin_id: int = None) -> Optional[Dict[str, Any]]:
    """
    确认收款 + 自动续期 VIP (累加逻辑)
    优先调用 Supabase RPC 存储过程 (事务安全)，失败则用 REST fallback
    """
    payment = get_payment_by_id(payment_id)
    if not payment:
        return {"ok": False, "error": "订单不存在"}
    if payment.get("status") != "pending":
        return {"ok": False, "error": f"订单状态为 {payment.get('status')}，非 pending"}

    user_id = payment["user_id"]
    plan_name = payment["plan_name"]
    months = payment["months"]

    # 方案 1: 尝试调用 RPC 存储过程 (事务安全)
    rpc_params = {"p_payment_id": payment_id}
    if admin_id:
        rpc_params["p_admin_id"] = admin_id
    rpc_res = _supabase_request("POST", "rpc/confirm_payment_and_renew", json_data=rpc_params)
    if rpc_res is not None and isinstance(rpc_res, (dict, list)):
        result = rpc_res[0] if isinstance(rpc_res, list) and len(rpc_res) > 0 else rpc_res
        if isinstance(result, dict) and result.get("ok"):
            return result

    # 方案 2: REST fallback (非事务，但功能完整)
    now = datetime.utcnow()
    now_iso = now.isoformat() + "Z"

    # 查询当前有效订阅到期时间
    params = {
        "user_id": f"eq.{user_id}",
        "status": "eq.active",
        "expires_at": f"gt.{now_iso}",
        "order": "expires_at.desc",
        "limit": "1"
    }
    existing = _supabase_request("GET", "subscriptions", params=params)
    current_expires = None
    if existing and isinstance(existing, list) and len(existing) > 0:
        try:
            exp_str = existing[0]["expires_at"].replace("Z", "")
            current_expires = datetime.fromisoformat(exp_str)
        except Exception:
            current_expires = None

    # 计算新到期时间 (累加逻辑)
    if current_expires and current_expires > now:
        new_expires = current_expires + timedelta(days=30 * months)
    else:
        new_expires = now + timedelta(days=30 * months)

    # 创建新订阅记录
    sub_data = {
        "user_id": user_id,
        "plan_name": plan_name,
        "status": "active",
        "start_at": now_iso,
        "expires_at": new_expires.isoformat() + "Z"
    }
    sub_res = _supabase_request("POST", "subscriptions", json_data=sub_data)
    if not sub_res:
        return {"ok": False, "error": "创建订阅失败"}

    # 更新订单状态
    update_data = {
        "status": "completed",
        "confirmed_at": now_iso
    }
    if admin_id:
        update_data["confirmed_by"] = admin_id
    _supabase_request("PATCH", f"payments?id=eq.{payment_id}", json_data=update_data)

    return {
        "ok": True,
        "expires_at": new_expires.isoformat() + "Z",
        "plan_name": plan_name
    }


def get_vip_remaining_days(user_id: int) -> Optional[int]:
    """获取 VIP 剩余天数 (None 表示无有效 VIP)"""
    sub = get_active_subscription(user_id)
    if not sub:
        return None
    try:
        exp_str = sub["expires_at"].replace("Z", "")
        exp_dt = datetime.fromisoformat(exp_str)
        delta = exp_dt - datetime.utcnow()
        return max(delta.days, 0)
    except Exception:
        return None


def get_vip_status_detail(user_id: int) -> Dict[str, Any]:
    """获取 VIP 完整状态: is_active, plan_name, expires_at, remaining_days"""
    sub = get_active_subscription(user_id)
    if not sub:
        return {"is_active": False, "plan_name": None, "expires_at": None, "remaining_days": None}
    remaining = get_vip_remaining_days(user_id)
    return {
        "is_active": True,
        "plan_name": sub.get("plan_name", ""),
        "expires_at": sub.get("expires_at", ""),
        "remaining_days": remaining
    }


def check_payments_table() -> bool:
    """检测 payments 表是否已创建 (用于前端提示)"""
    params = {"id": "eq.0", "select": "id", "limit": "1"}
    res = _supabase_request("GET", "payments", params=params)
    return isinstance(res, list)

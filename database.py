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

def _supabase_request(method: str, endpoint: str, params: dict = None, json_data: Any = None,
                      headers_extra: dict = None, return_error: bool = False) -> Any:
    """封装 Supabase PostgREST 请求。

    `return_error=True` 时返回 `(data, err)`，`err` 为 `None` 或
    `{"status": int, "code": str, "message": str, "detail": str}`。

    为什么需要这个开关：默认把所有 HTTPError 一律吞成 `None`，调用方只知道
    「没成功」，不知道为什么。于是提示文案只能猜一个最常见的原因写死——
    实际发生 409 唯一冲突（同一微信想绑第二个账号）时，用户会看到
    「管理员需执行 init_wxpusher_column.sql 补列」。**失败如实上报了，
    但归因是编的**，这比不报更糟：它把人推向一个完全无关的方向，
    管理员照着去执行迁移脚本，脚本幂等地成功，问题一点没变。
    """
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
            data = json.loads(body) if body else True
            return (data, None) if return_error else data
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"[Supabase HTTP Error] {method} {url} -> {e.code}: {err_body}")
        if not return_error:
            return None
        info = {}
        try:
            parsed = json.loads(err_body)
            if isinstance(parsed, dict):
                info = parsed
        except Exception:
            pass
        return None, {
            "status": e.code,
            "code": str(info.get("code") or ""),
            "message": str(info.get("message") or ""),
            "detail": str(info.get("details") or info.get("detail") or ""),
        }
    except Exception as e:
        print(f"[Supabase Connection Error] {e}")
        if not return_error:
            return None
        return None, {"status": 0, "code": "", "message": str(e), "detail": ""}

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
    """更新保存用户自选股。

    返回 False 表示**没有落库**（例如 users 表缺 watchlist 列，PostgREST 报
    PGRST204）。调用方必须把 False 当作失败提示给用户，不能显示成
    「已在本地更新」——自选股只存在 session_state 里，刷新即丢失，
    而个性化摘要靠它取数，静默失败会让付费推送退化成免费内容。
    缺列时执行 init_watchlist_column.sql 补上。
    """
    cleaned = list(dict.fromkeys([c.strip().upper() for c in watchlist if c.strip()]))
    res = _supabase_request("PATCH", f"users?id=eq.{user_id}",
                            json_data={"watchlist": cleaned})
    return res is not None


# ==================== 微信推送绑定 (WxPusher UID) ====================

def explain_uid_write_error(err: Optional[Dict[str, Any]]) -> str:
    """把 PostgREST 的写库错误翻译成「用户能照着做下一步」的话。

    不做这层翻译的后果不是提示不够友好，而是**提示把人指向错误的方向**：
    唯一冲突（同一微信绑第二个账号）曾被一律说成「管理员需补 wxpusher_uid 列」，
    管理员照着执行幂等的迁移脚本，脚本成功，问题分毫未动。
    归因错误的错误信息比「未知错误」更贵。
    """
    if not err:
        return "云端保存失败，原因未知。"
    status = int(err.get("status") or 0)
    code = str(err.get("code") or "")
    blob = f"{code} {err.get('message', '')} {err.get('detail', '')}".lower()

    # 23505 = unique_violation；PostgREST 以 409 透出
    if status == 409 or code == "23505" or "duplicate key" in blob or "uniq_users_wxpusher_uid" in blob:
        return ("这个微信已经绑定在另一个账号上了。"
                "一个微信只能绑一个账号（否则你会收到多份不同的摘要）。"
                "请先用那个账号登录并解除绑定，或换一个微信扫码。")
    # PGRST204 = 列不存在。括号显式写出：`a or b and c` 里 and 优先，
    # 不加括号虽然结果一样，但读起来像另一个意思，下次改动极易改错优先级。
    if code == "PGRST204" or ("wxpusher_uid" in blob and "column" in blob):
        return ("云端数据表还缺 wxpusher_uid 列，"
                "需要管理员在 Supabase 里执行一次 init_wxpusher_column.sql。")
    if status == 0:
        return f"连不上云端数据库：{err.get('message') or '网络异常'}。请稍后重试。"
    if status in (401, 403):
        return "云端拒绝了这次写入（凭据或权限问题），请联系管理员。"
    return f"云端保存失败（HTTP {status}{': ' + code if code else ''}），请稍后重试。"


def bind_wxpusher_uid(user_id: int, uid: Optional[str]) -> tuple:
    """写入/清空 UID，返回 `(是否成功, 失败原因文案)`。

    绑定是「主动触达」这一段的唯一入口，页面显示成功而库里没有，
    用户会以为自己已经订阅了推送，然后每天等一条永远不会来的消息。
    所以既要忠实返回失败，也要把失败的**真实**原因带出来。
    """
    res, err = _supabase_request("PATCH", f"users?id=eq.{user_id}",
                                 json_data={"wxpusher_uid": (uid or None)},
                                 return_error=True)
    if res is None:
        return False, explain_uid_write_error(err)
    # `Prefer: return=representation` 下零行命中返回 `[]`，而 `[] is not None` 为真
    # —— 只判 None 会把「账号根本不存在」当成绑定成功。
    if isinstance(res, list) and len(res) == 0:
        return False, "云端没有更新到任何一行（账号可能已不存在），请重新登录后再试。"
    return True, ""


def update_user_wxpusher_uid(user_id: int, uid: Optional[str]) -> bool:
    """布尔版（保留给不关心原因的调用方）。需要提示文案时用 bind_wxpusher_uid。"""
    res = _supabase_request("PATCH", f"users?id=eq.{user_id}",
                            json_data={"wxpusher_uid": (uid or None)})
    return res is not None


def get_user_wxpusher_uid(user_id: int) -> Optional[str]:
    u = get_user_by_id(user_id)
    return (u or {}).get("wxpusher_uid") or None


def get_admin_wxpusher_uids() -> Optional[List[str]]:
    """所有已绑微信的管理员 UID。None = 取数失败，[] = 确实没人绑。

    区分这两者的意义：告警通道自己坏了（凭据/网络）必须能被看出来，
    不能跟「站长还没扫码」混成同一个空列表——前者要修，后者要绑。
    """
    res = _supabase_request("GET", "users",
                            params={"select": "wxpusher_uid", "is_admin": "eq.true"})
    if res is None:
        return None
    out = []
    for r in (res or []):
        u = (r or {}).get("wxpusher_uid")
        if u and str(u).strip():
            out.append(str(u).strip())
    return out


def get_push_recipients() -> Optional[List[Dict[str, Any]]]:
    """返回有效订阅且已绑定微信的投递名单。

    返回 None 表示**取数本身失败**（凭据/网络/表结构问题），与「确实没人订阅」
    返回 [] 必须区分开——前者要报警，后者是正常状态。把两者都返回 []
    会让「配置坏了」长期伪装成「暂时没人付费」。

    到期列名是 expires_at（不是 end_date），此处曾写错导致有效订阅恒为 0。

    权限判定必须与 auth.is_vip() 一致：那边「管理员默认具备 VIP 权限」，
    如果这里只认订阅表，管理员就会出现「站内看得到会员功能、却永远收不到推送」
    的分裂状态——同一个「谁是会员」的问题不允许有两套答案。
    """
    subs = get_all_subscriptions()
    if subs is None:
        return None
    if subs and "expires_at" not in subs[0]:
        return None                       # 表结构不符：宁可报错也不静默返回空
    today = datetime.utcnow().date().isoformat()
    active_ids = set()
    for s in subs:
        if str(s.get("status") or "active") != "active":
            continue
        end = str(s.get("expires_at") or "")[:10]
        if end and end >= today:
            active_ids.add(s.get("user_id"))

    # 管理员与 auth.is_vip() 对齐：无需订阅记录即视为有效会员
    admins = _supabase_request("GET", "users", params={"select": "id", "is_admin": "eq.true"})
    if admins is None:
        return None                       # 名单不完整就不算成功，宁可报错
    for a in admins:
        if a.get("id") is not None:
            active_ids.add(a["id"])

    out = []
    for uid in active_ids:
        u = get_user_by_id(uid)
        if not u:
            continue
        # 付费不等于同意被打扰：optin 显式为 False 就跳过
        if u.get("digest_optin") is False:
            continue
        out.append({
            "user_id": uid,
            "email": u.get("email"),
            "wxpusher_uid": u.get("wxpusher_uid") or None,
            "watchlist": get_user_watchlist(uid) or [],
        })
    return out


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

# -*- coding: utf-8 -*-
"""密码重置码 —— 生成、加盐哈希、校验、通道投递（计算层）。

**本模块不 import streamlit**：它要被页面调用，也要能被脱 runtime 的
探针直接跑（自检脚本没有 Streamlit 运行时）。凭据桥接（st.secrets →
环境变量）由页面负责。

设计红线（逐条都在代码里有对应实现，改动前先读这段）：

1. **明文码绝不落库、绝不进日志**。库里只存 `salt` 与 `hash`，
   码本身只出现在「发给用户的那一条消息」里。存明文等于把「谁都能看
   一眼数据库就拿到任意账号重置凭证」这件事做成常态。

2. **码空间必须够大，且不能用容易看错的字符**。
   6 位纯数字只有 10^6，配上一个可离线爆破的哈希就是纸糊的。这里用
   32 字符集（去掉了 I/O/0/1 —— 这四个是最经典的抄错来源）取 10 位，
   空间 32^10 ≈ 1.1e15。代价是用户要复制粘贴，而链接形式正好补上了
   这一点体验。

3. **有效期短 + 尝试次数上限 + 单次消费**。三者缺一不可：
   只有有效期挡不住「15 分钟内暴力猜」；只有尝试上限挡不住「慢慢试」；
   只有单次消费不解决前两个。这三条由数据层配合完成（见 database.py
   的 count/bump/consume 三个函数）。

4. **投递失败必须如实返回**。返回 True 而实际没送到，用户会去翻一个
   永远不会来的消息——比直接告诉他「没发出去，走人工」糟糕得多。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

# 32 字符集：A-Z 去掉 I 和 O，2-9 去掉 0 和 1。
# 为什么不是完整 36 字符：口令码是给人抄的，不是给机器读的。
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10

CODE_TTL_MINUTES = 15          # 有效期
MAX_VERIFY_ATTEMPTS = 5        # 单条码最多允许试错几次，超限即作废
RESEND_COOLDOWN_SECONDS = 60   # 同一账号两次请求的最小间隔（防轰炸）
MAX_REQUESTS_PER_HOUR = 5      # 同一账号每小时请求上限

# 站址：用于在邮件/微信里拼「一点即填」的重置链接。
# 未配置时不发链接、只发码（页面会引导用户手动输入）——
# 绝不回落到硬编码域名：站点改名后链接会静默指向一个不存在的地址，
# 而用户看到的只是「点了没反应」，排查方向会被完全带偏。
SITE_URL_ENV = "SITE_URL"


def site_url() -> str:
    """站点根地址（末尾不带 /）。未配置返回空串。"""
    val = (os.getenv(SITE_URL_ENV) or "").strip().rstrip("/")
    if val and not val.startswith(("http://", "https://")):
        # 只填了域名（chilam.club）时补上协议——否则拼出来的链接是相对路径，
        # 在邮件客户端里会变成 404 而不是报错。
        val = "https://" + val
    return val


def reset_link(code: str) -> str:
    """一步到位的重置链接。未配置 SITE_URL 时返回空串。

    路径指向 `auth` 页（Streamlit 对 pages/auth.py 的公开路径），
    带着码落到「忘记密码」表单并自动预填。**链接里不带邮箱**：
    邮箱仍需用户自己填一遍，等于「码 + 邮箱」双重匹配才生效——
    码被旁路看到也不足以重置别人账号。
    """
    base = site_url()
    if not base:
        return ""
    return f"{base}/auth?reset={code}"


def generate_code() -> str:
    """生成一枚重置码。用 secrets，不用 random（后者可被预测）。"""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw: str) -> str:
    """把用户输入归一化：去空白/连字符、转大写。

    **刻意不做 O→0 / I→1 这类纠错映射**：字符集里本来就没有 O/I/0/1，
    出现它们说明用户抄错了别的字符，而错成什么无法可靠推断（O 可能是 D
    也可能是 Q）。猜错的后果是「提示码不正确」，用户会怀疑自己没抄错、
    转而怀疑系统；不如老老实实报错，让他重新复制一次。
    """
    s = str(raw or "").strip().upper()
    return "".join(ch for ch in s if ch.isalnum())


def new_salt() -> str:
    return secrets.token_bytes(16).hex()


def hash_code(code: str, salt: str) -> str:
    """盐 + 码 的 sha256。

    为什么不是 PBKDF2（本仓库口令用的是 PBKDF2）：
    PBKDF2 的高迭代次数是用来对抗「口令空间小、可字典爆破」的；
    重置码有 1.1e15 空间、15 分钟寿命、5 次尝试上限，在线爆破不成立，
    离线爆破也需要先拖走整库。此时用 sha256+随机盐足够，且不会让
    「用户等码」的路径上多几百毫秒 CPU。
    """
    payload = f"{salt}:{normalize_code(code)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_code(code: str, salt: str, expected_hash: str) -> bool:
    """常量时间比对。用 compare_digest 而非 ==，避免时序侧信道。"""
    if not (salt and expected_hash):
        return False
    return hmac.compare_digest(hash_code(code, salt), str(expected_hash))


def masked_email(email: str) -> str:
    """a***@qq.com —— 用于「码已发往 …」的提示，避免在屏幕上完整回显邮箱
    （用户可能正在投屏/录屏，而这是与账号直接绑定的敏感信息）。"""
    s = str(email or "").strip()
    if "@" not in s:
        return s[:1] + "***" if s else ""
    name, _, domain = s.partition("@")
    if len(name) <= 1:
        shown = name[:1] + "***"
    elif len(name) <= 3:
        shown = name[:1] + "***"
    else:
        shown = name[:2] + "***" + name[-1:]
    return f"{shown}@{domain}"


# ==================== 投递文案 ====================

def _wx_body(code: str, minutes: int) -> str:
    """微信推送正文（Markdown）。"""
    return (
        "## 🔑 Chilam Club 密码重置\n\n"
        f"你的重置码是：**{code}**\n\n"
        f"- 有效期 **{minutes} 分钟**，用过即失效\n"
        "- 输入时需要同时填写你的注册邮箱\n\n"
        "**如果不是你本人操作**，请忽略本条消息，并尽快登录后修改密码；"
        "你的账号密码不会被本条消息改变。"
    )


def _mail_text(code: str, minutes: int, link: str = "") -> str:
    lines = [
        "Chilam Club 密码重置",
        "",
        f"你的重置码：{code}",
        f"有效期 {minutes} 分钟，使用一次后立即失效。",
        "",
    ]
    if link:
        lines += ["在浏览器打开下面的链接可以自动填入重置码：", link, ""]
    lines += [
        "重置时需要同时填写你的注册邮箱，码与邮箱必须匹配。",
        "",
        "如果这不是你本人的操作，请忽略本邮件——你的密码不会被改变。",
        "",
        "— Chilam Club",
    ]
    return "\n".join(lines)


def _mail_html(code: str, minutes: int, link: str = "") -> str:
    btn = ""
    if link:
        btn = (f"<p style='margin:18px 0;'><a href=\"{link}\" "
               "style='background:#d93025;color:#fff;padding:11px 22px;"
               "border-radius:6px;text-decoration:none;font-weight:600;'>"
               "打开重置页面</a></p>"
               "<div style='color:#888;font-size:12px;word-break:break-all;'>"
               f"链接打不开时手动复制：{link}</div>")
    return (
        "<div style=\"font-family:-apple-system,'PingFang SC','Microsoft YaHei',"
        "sans-serif;font-size:15px;line-height:1.75;color:#222;max-width:560px;\">"
        "<h2 style='margin:0 0 6px;'>Chilam Club 密码重置</h2>"
        "<p style='color:#666;margin:0 0 18px;'>这是一封自动发送的邮件，请勿直接回复。</p>"
        "<p>你的重置码：</p>"
        f"<div style='font-size:30px;font-weight:700;letter-spacing:5px;"
        "background:#f5f5f7;padding:16px 20px;border-radius:8px;"
        f"text-align:center;font-family:Consolas,Menlo,monospace;'>{code}</div>"
        f"<p style='margin:16px 0 0;'>有效期 <strong>{minutes} 分钟</strong>，"
        "使用一次后立即失效。重置时需同时填写你的注册邮箱，码与邮箱必须匹配。</p>"
        f"{btn}"
        "<hr style='border:none;border-top:1px solid #e5e5e5;margin:22px 0;'>"
        "<p style='color:#888;font-size:13px;'>如果这不是你本人的操作，请忽略本邮件"
        "——你的密码不会被改变。</p></div>"
    )


# ==================== 投递 ====================

def deliver_wxpusher(uid: str, code: str,
                     minutes: int = CODE_TTL_MINUTES) -> tuple[bool, str]:
    """推给已绑定微信的用户。返回 (是否送达, 说明)。"""
    if not (uid or "").strip():
        return False, "该账号未绑定微信"
    try:
        import wxpusher as wx
    except Exception as e:
        return False, f"推送模块不可用：{type(e).__name__}"
    okd, bad = wx.send_to_uids([uid], _wx_body(code, minutes),
                               "🔑 Chilam Club 密码重置码")
    if okd:
        return True, "已推送到微信"
    return False, "；".join(bad) or "微信推送未送达"


def deliver_email(email: str, code: str,
                  minutes: int = CODE_TTL_MINUTES) -> tuple[bool, str]:
    """发到用户邮箱。返回 (是否送达, 说明)。"""
    import mailer
    if not mailer.is_configured():
        return False, "站点未配置邮件通道"
    link = reset_link(code)
    ok, note = mailer.send_mail(
        email, "【Chilam Club】密码重置码",
        _mail_text(code, minutes, link), _mail_html(code, minutes, link))
    return ok, note

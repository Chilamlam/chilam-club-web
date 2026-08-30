"""
WxPusher 推送封装（纯 stdlib，不 import streamlit，可脱 runtime 单测）

## 为什么需要这个模块，而不是继续用 Server酱

Server酱 的 SendKey 是「一把 key 绑一个微信账号」的身份凭据，官方文档明确
「群发：不支持」。它推不到第三方用户手上，只能推给站长自己。用它做用户投递
在架构上就是死路，不是配置问题。

WxPusher 的模型不同：**一个应用 appToken + 每个用户一个 UID**。用户扫码关注
一次，我们拿到他的 UID，之后按 UID 精准投递，单次请求可带 2000 个 UID。
这是目前唯一免费、能一对多推到微信、且用户不用装企业微信/进通讯录的通道。

## 三个必须显式处理的坑

1. **整体成功 ≠ 单个成功**。发送接口在整体 `code=1000 success=true` 的同时，
   `data[]` 里每个 UID 还各有自己的 `code`。实测给一个不存在的 UID 发消息：

       {"code":1000,"msg":"处理成功","success":true,
        "data":[{"uid":"UID_notexist","code":1001,
                 "status":"用户UID=[UID_notexist]不存在"}]}

   只看顶层 `code` 会把「这个人根本收不到」记成推送成功。所以 `send_to_uids`
   逐条校验 `data[].code`，返回 (成功 UID 列表, 失败明细)。

2. **二维码接口返回的 code 是一次性凭据，不是 UID**。流程是
   `create_qrcode(extra=业务id)` → 用户扫码关注 → `scan_uid(code)` 才拿到 UID。
   未扫码时返回 `{"code":1001,"msg":"暂无用户扫描二维码"}`，这是**正常的
   等待态**，不是错误——调用方必须能区分「还没扫」和「接口挂了」，
   否则会把等待渲染成一个红色报错，用户以为绑定失败就走了。

3. **本机 urllib 会被 TLS 中途打断**（`UNEXPECTED_EOF_WHILE_READING`），
   而 curl 稳定 200。GitHub Actions 上 urllib 正常。所以统一先 urllib、
   传输层异常时降级 curl；HTTPError（服务端明确拒绝）不兜底，
   否则会把 4xx 语义错误重试成看起来像网络抖动。
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request

API = "https://wxpusher.zjiecode.com/api"

# 官方限制：单次请求最多 2000 个 UID
MAX_UIDS_PER_CALL = 2000
# 通知栏摘要上限（超出会被服务端截断，且不允许换行）
SUMMARY_MAX = 95

CT_TEXT, CT_HTML, CT_MARKDOWN = 1, 2, 3


def app_token() -> str:
    """appToken 只从环境变量取，绝不写进代码或日志。"""
    return (os.getenv("WXPUSHER_APP_TOKEN") or "").strip()


# ================= HTTP =================

def _curl(url: str, body: dict | None) -> tuple[bool, str]:
    cmd = ["curl", "-s", "--max-time", "25", url]
    if body is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json",
                "-d", json.dumps(body, ensure_ascii=False)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        return (r.returncode == 0 and bool(r.stdout)), (r.stdout or r.stderr)[:2000]
    except Exception as e:
        return False, f"curl failed: {type(e).__name__}"


def _request(url: str, body: dict | None = None, timeout: int = 20) -> tuple[bool, dict | str]:
    """返回 (是否拿到 JSON, JSON 或错误文本)。

    注意「拿到 JSON」不等于「业务成功」——业务成败由调用方看 code 字段判定。
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=("POST" if data else "GET"),
        headers={"Content-Type": "application/json", "User-Agent": "chilam-club/1.0"})
    raw = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        # 服务端明确拒绝：不兜底，直接把语义交回调用方
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:500]}"
    except Exception:
        ok, raw = _curl(url, body)          # 传输层异常才降级 curl
        if not ok:
            return False, str(raw)[:500]
    try:
        return True, json.loads(raw)
    except Exception:
        return False, f"响应非 JSON：{str(raw)[:300]}"


# ================= 绑定流程 =================

def create_qrcode(extra: str, valid_seconds: int = 1800) -> tuple[str, str] | tuple[None, str]:
    """创建参数二维码，返回 (图片URL, 一次性 code) 或 (None, 错误说明)。

    `extra` 会在用户扫码后原样回传，用它装 user_id，这样多个用户同时绑定也不会串。
    """
    tok = app_token()
    if not tok:
        return None, "未配置 WXPUSHER_APP_TOKEN"
    ok, res = _request(f"{API}/fun/create/qrcode",
                       {"appToken": tok, "extra": str(extra),
                        "validTime": int(valid_seconds)})
    if not ok:
        return None, f"接口不可用：{res}"
    if not isinstance(res, dict) or res.get("code") != 1000:
        return None, f"创建失败：{(res or {}).get('msg') if isinstance(res, dict) else res}"
    d = res.get("data") or {}
    url, code = d.get("url") or d.get("shortUrl"), d.get("code")
    if not (url and code):
        return None, "返回缺少 url/code 字段"
    return url, code


def scan_uid(code: str) -> tuple[str | None, str]:
    """轮询扫码结果，返回 (uid 或 None, 状态说明)。

    「暂无用户扫描二维码」是等待态而不是失败——必须让调用方能区分，
    否则等待会被渲染成报错，用户以为绑定坏了。
    """
    if not code:
        return None, "缺少二维码 code"
    ok, res = _request(f"{API}/fun/scan-qrcode-uid?code={code}")
    if not ok:
        return None, f"接口不可用：{res}"
    if not isinstance(res, dict):
        return None, "返回格式异常"
    if res.get("code") == 1000 and res.get("data"):
        return str(res["data"]), "ok"
    msg = str(res.get("msg") or "")
    if "暂无用户扫描" in msg:
        return None, "waiting"
    return None, msg or "未知状态"


def list_followers(page: int = 1, page_size: int = 100) -> tuple[list[dict], str]:
    """查询已关注用户（用于核对绑定关系，不参与投递）。"""
    tok = app_token()
    if not tok:
        return [], "未配置 WXPUSHER_APP_TOKEN"
    ok, res = _request(f"{API}/fun/wxuser/v2?appToken={tok}&page={page}&pageSize={page_size}")
    if not ok or not isinstance(res, dict) or res.get("code") != 1000:
        return [], f"查询失败：{res if not isinstance(res, dict) else res.get('msg')}"
    return list((res.get("data") or {}).get("records") or []), "ok"


# ================= 投递 =================

def _clean_summary(text: str) -> str:
    """通知栏摘要强制单行并截断——含换行会被服务端拒绝或显示错乱。"""
    return " ".join(str(text or "").split())[:SUMMARY_MAX]


def send_to_uids(uids: list[str], content: str, summary: str,
                 content_type: int = CT_MARKDOWN,
                 url: str | None = None) -> tuple[list[str], list[str]]:
    """按 UID 投递，返回 (成功的 uid 列表, 失败明细列表)。

    空内容一律拒发：宁可今天不推，也不推一条「今天没有数据」的骚扰消息。
    """
    tok = app_token()
    if not tok:
        return [], ["未配置 WXPUSHER_APP_TOKEN"]
    uids = [str(u).strip() for u in (uids or []) if str(u or "").strip()]
    if not uids:
        return [], []                      # 没有收件人不算失败
    if not str(content or "").strip():
        return [], ["内容为空，拒绝发送"]

    okd, bad = [], []
    for i in range(0, len(uids), MAX_UIDS_PER_CALL):
        chunk = uids[i:i + MAX_UIDS_PER_CALL]
        body = {"appToken": tok, "content": content,
                "summary": _clean_summary(summary),
                "contentType": int(content_type), "uids": chunk}
        if url:
            body["url"] = url
        got, res = _request(f"{API}/send/message", body, timeout=30)
        if not got or not isinstance(res, dict):
            bad.append(f"{len(chunk)} 个 UID 请求失败：{res}")
            continue
        if res.get("code") != 1000:
            bad.append(f"{len(chunk)} 个 UID 被拒：{res.get('msg')}")
            continue
        items = res.get("data")
        if not isinstance(items, list) or not items:
            # 顶层成功但没有逐条结果 → 无法确认送达，按失败记，不假装成功
            bad.append(f"{len(chunk)} 个 UID 无投递明细，无法确认送达")
            continue
        for it in items:
            uid = str((it or {}).get("uid") or "")
            if (it or {}).get("code") == 1000:
                okd.append(uid)
            else:
                bad.append(f"{uid}: {(it or {}).get('status') or (it or {}).get('code')}")
    return okd, bad

# -*- coding: utf-8 -*-
"""管理员告警通道（站内实时事件用，与跑批共用同一套端点推导）。

为什么必须有这个模块（2026-08-30）：
  付费链路的最后一步是「管理员确认收款」，全靠人工。而在此之前，站长没有
  任何被动知会的手段——订单落进 payments 表就静静躺着，用户付了钱在等，
  站长不打开后台永远不知道。这不是体验问题，是**收了钱不发货**。
  「等站长自己想起来去看后台」不是流程，是运气。

通道选择顺序（先 WxPusher 后 Server酱）：
  · WxPusher：管理员本人已扫码绑定，额度宽松（千条/日级别），首选。
  · Server酱：免费版 5 条/天，是兜底——管理员还没绑微信时至少有条路。
  两条都试，只要有一条成功就算送达；两条全失败必须**如实返回失败**，
  由调用方把「请手动联系管理员」的话说给用户，而不是假装已经通知到。

端点推导逻辑集中在这里，`daily_digest.py` 反过来引用本模块，
避免同一份 SendKey 前缀规则在两处各自漂移（那种分叉只会在某一侧出错时
才被发现，而且表现成"网络问题"）。

本模块**不 import streamlit**：跑批环境里没有 runtime。
st.secrets → 环境变量的桥接由调用方（页面）负责。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

SERVERCHAN_ENV = "DIGEST_SERVERCHAN_KEY"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def serverchan_url(key: str) -> str:
    """按 SendKey 前缀推导推送端点——两个产品线的 key 不通用、域名也不同。

    · Turbo(SCT)：key 形如 `SCTxxxx`，端点 `https://sctapi.ftqq.com/<key>.send`
    · Server酱³(SC3)：key 形如 `sctp{uid}t{rand}`，端点
      `https://{uid}.push.ft07.com/send/<key>.send`——**uid 必须从 key 里抠出来
      拼进域名**。域名写错不会报"key 无效"，而是整个域名解析不到，
      表现成网络错误，很容易误判成"网络问题、重试就好"。
    """
    if key.startswith("sctp"):
        uid = ""
        for ch in key[4:]:
            if not ch.isdigit():
                break
            uid += ch
        if uid:
            return f"https://{uid}.push.ft07.com/send/{key}.send"
    return f"https://sctapi.ftqq.com/{key}.send"


def post_json(url: str, body: dict, timeout: int = 15) -> tuple[bool, str]:
    """POST JSON，返回 (HTTP 是否 2xx, 响应体文本)。

    响应体截断长度取 800 而非 200：Server酱 的成败判定要靠响应体里的 `code`
    字段，截太短会把 JSON 切断导致解析失败，进而把失败误判成成功。
    """
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.read().decode("utf-8", "ignore")[:800]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:800]}"
    except Exception as e:
        return False, str(e)


def send_serverchan(title: str, body: str) -> tuple[bool, str]:
    """返回 (是否送达, 说明)。未配置 key 时返回 (False, "未配置…")。

    两个坑，都会让"失败"伪装成"成功"，必须显式处理：

    1. **额度耗尽/参数错误时 HTTP 仍是 200**，真正结果在响应体 `code` 字段
       （0 = 成功，非 0 时 `message` 说明原因）。只看 HTTP 状态码会把
       "今天配额没了"记成推送成功，明天照样静默失败。
    2. **title 不允许包含换行符**，含换行会被服务端拒绝并报"包含特殊字符"。
       正文放 desp，标题强制压成单行。
    """
    key = _env(SERVERCHAN_ENV)
    if not key:
        return False, f"未配置 {SERVERCHAN_ENV}"
    one_line = " ".join(str(title).split())[:100]
    ok, msg = post_json(serverchan_url(key), {"title": one_line, "desp": body})
    if ok:
        try:
            resp = json.loads(msg)
            code = resp.get("code")
            if code not in (0, None):
                return False, f"code={code} {resp.get('message', '')}"
        except Exception:
            pass       # 返回体不是 JSON 时以 HTTP 状态为准，不因解析失败误判
    return ok, ("ok" if ok else msg)


def send_wxpusher_admins(title: str, body: str) -> tuple[bool, str]:
    """推给所有已绑微信的管理员。返回 (是否至少一人送达, 说明)。

    没有管理员绑定 → (False, "无已绑定的管理员")，这是**待办不是异常**：
    说明站长自己还没扫码，应该去绑，而不是当成推送故障排查。
    """
    try:
        import database
        import wxpusher as wx
    except Exception as e:
        return False, f"模块不可用：{e}"

    try:
        uids = database.get_admin_wxpusher_uids()
    except Exception as e:
        return False, f"管理员名单取数失败：{e}"
    if uids is None:
        return False, "管理员名单取数失败"
    if not uids:
        return False, "无已绑定微信的管理员"

    okd, bad = wx.send_to_uids(uids, body, title)
    if okd:
        return True, f"{len(okd)}/{len(uids)} 位管理员已送达"
    return False, "；".join(bad) or "全部投递失败"


def notify_admins(title: str, body: str) -> tuple[bool, str]:
    """双通道尝试，返回 (是否送达, 逐通道说明)。

    调用方必须尊重返回的 False——付费场景下，「以为通知到了其实没通知到」
    会让用户白等，比直接告诉他"请手动联系管理员"糟糕得多。
    """
    notes = []
    delivered = False

    ok, msg = send_wxpusher_admins(title, body)
    notes.append(f"wxpusher: {msg}")
    delivered = delivered or ok

    ok2, msg2 = send_serverchan(title, body)
    notes.append(f"serverchan: {msg2}")
    delivered = delivered or ok2

    return delivered, "；".join(notes)

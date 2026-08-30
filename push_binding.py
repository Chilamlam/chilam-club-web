# -*- coding: utf-8 -*-
"""微信推送绑定组件（可在多个页面复用）。

为什么要单独抽出来（2026-08-30）：
  绑定是「主动触达」这段付费价值的**唯一开关**，之前它只存在于「我的池子」
  页面一个默认折叠的 expander 里。付费用户走完「下单 → 付款 → 管理员确认 →
  VIP 生效」全程，一次都不会被告知还有这一步；结果是钱付了、权限开了、
  推送永远不来——而且在后台日志里长得跟「这个人还没订阅」完全一样。

  所以绑定入口必须出现在**所有会让用户以为「我已经订阅好了」的位置**：
  会员中心（刚付完钱）、收盘摘要页（正在看内容）、我的池子（配置自选）。
  同一份 UI 三处复用，靠 key_prefix 区分控件，避免三份实现各自漂移。

刻意不放进 wxpusher.py：那个模块要在 GitHub Actions 里跑，不能 import
streamlit。这里是纯展示层，反过来不做任何网络约定之外的业务判断。
"""
from __future__ import annotations

import os

import streamlit as st

import database

_QR_VALID_SECONDS = 1800


def ensure_app_token() -> bool:
    """把 st.secrets 里的 appToken 桥到环境变量。

    `wxpusher.py` 刻意不 import streamlit（否则没法脱 runtime 单测，也没法在
    GitHub Actions 里跑），所以它只认环境变量。本地/Cloud 运行时凭据在
    st.secrets 里，需要在这里搭一次桥。已存在则不覆盖——Actions 环境里
    环境变量才是唯一来源。
    """
    if os.getenv("WXPUSHER_APP_TOKEN"):
        return True
    tok = ""
    try:
        tok = str(st.secrets.get("WXPUSHER_APP_TOKEN", "")).strip()
    except Exception:
        tok = ""
    if not tok:
        try:
            tok = str((st.secrets.get("wxpusher") or {}).get("app_token", "")).strip()
        except Exception:
            tok = ""
    if tok:
        os.environ["WXPUSHER_APP_TOKEN"] = tok
        return True
    return False


def is_bound(user_id: int) -> bool | None:
    """True 已绑 / False 未绑 / None 查不到（取数失败，不等于未绑）。

    区分 False 与 None 是为了不在数据库抖动时冲用户喊「你还没绑定」——
    那种误报会让人反复重扫，最后不信任这个提示。
    """
    try:
        return bool(database.get_user_wxpusher_uid(user_id))
    except Exception:
        return None


def _flash() -> None:
    msg = st.session_state.pop("push_flash", None)
    if not msg:
        return
    kind, text = msg
    (st.success if kind == "ok" else st.error)(text)


def render(user_id: int, *, key_prefix: str) -> None:
    """绑定/解绑 UI。key_prefix 用于隔离同一 session 内多处渲染的控件 key。

    走「参数二维码」而不是让用户自己复制 UID：复制粘贴 `UID_xxxx` 是纯摩擦，
    且极易贴错（粘到空格、贴一半），错了之后表现是「绑定成功但永远收不到」
    ——最难排查的那种失败。参数二维码把 user_id 塞进 extra，用户扫完直接从
    服务端读回 UID，全程零输入。

    「暂无用户扫描二维码」是**等待态不是错误**，必须分开显示，
    否则用户看到红色报错就以为绑定失败走了。
    """
    _flash()

    if not ensure_app_token():
        st.info("站点尚未配置微信推送通道，暂不可绑定。")
        return

    import wxpusher as wx

    bound = is_bound(user_id)
    if bound is None:
        st.warning("⚠️ 暂时读不到你的绑定状态（云端取数失败），请稍后刷新再看。"
                   "这不代表绑定已失效。")
        return

    if bound:
        uid = database.get_user_wxpusher_uid(user_id) or ""
        st.success(f"✅ 已绑定微信推送（UID 尾号 …{str(uid)[-6:]}）。"
                   "每个交易日收盘后会把当日摘要推到你的微信。")
        if st.button("解除绑定 🔓", key=f"{key_prefix}_wx_unbind"):
            ok = database.update_user_wxpusher_uid(user_id, None)
            st.session_state["push_flash"] = (
                ("ok", "已解除微信推送绑定。") if ok else
                ("err", "⚠️ 解绑未能写入云端，请稍后重试。"))
            st.rerun()
        return

    st.caption("绑定后每个交易日收盘会把当日摘要推到微信，含用你自己自选股算的个性化段落。"
               "扫码即完成，无需复制任何内容。")

    qr_key = f"{key_prefix}_wx_qr"
    if st.button("生成绑定二维码 📷", key=f"{key_prefix}_wx_qr_gen"):
        url, code = wx.create_qrcode(extra=str(user_id), valid_seconds=_QR_VALID_SECONDS)
        if not url:
            st.session_state.pop(qr_key, None)
            st.error(f"❌ 二维码生成失败：{code}")
        else:
            st.session_state[qr_key] = {"url": url, "code": code}
        st.rerun()

    qr = st.session_state.get(qr_key)
    if not qr:
        return

    c1, c2 = st.columns([1, 2])
    with c1:
        # 用固定 width 而非 use_container_width：后者三代改名过，会整页 TypeError
        st.image(qr["url"], width=200)
    with c2:
        st.markdown(
            "1. 用**微信**扫上方二维码\n"
            "2. 关注弹出的「WxPusher 消息推送服务」\n"
            "3. 回到这里点下面的按钮完成绑定\n\n"
            "_二维码 30 分钟内有效。_")
        if st.button("我已扫码，完成绑定 ✅", key=f"{key_prefix}_wx_qr_confirm"):
            uid, status = wx.scan_uid(qr["code"])
            if uid:
                ok = database.update_user_wxpusher_uid(user_id, uid)
                st.session_state.pop(qr_key, None)
                st.session_state["push_flash"] = (
                    ("ok", "✅ 微信推送绑定成功，今晚收盘后就能收到第一条摘要。") if ok else
                    ("err", "⚠️ 已扫码但云端保存失败，绑定未生效。"
                            "管理员需执行 init_wxpusher_column.sql 补上 wxpusher_uid 列。"))
                st.rerun()
            elif status == "waiting":
                st.warning("还没检测到扫码。请先在微信里完成扫码并关注，再点这个按钮。")
            else:
                st.error(f"❌ 绑定失败：{status}")


def render_gate(user_id: int, *, key_prefix: str, context: str = "") -> bool:
    """给「已付费但未绑定」的用户一个躲不开的提示。返回 True 表示已绑定。

    用 st.error 而不是 st.info：这不是可有可无的小贴士，而是
    「你的会员权益现在有一半没有生效」。语气必须与后果匹配，
    否则用户会跟其他提示一起划过去。
    """
    bound = is_bound(user_id)
    if bound:
        return True
    if bound is None:
        st.caption("（暂时读不到微信绑定状态，稍后刷新可见。）")
        return False
    st.error("🔔 **推送还没有生效：你还没绑定微信。**\n\n"
             "会员权益里的「收盘后自动送到手上」需要绑定一次微信才能投递，"
             "否则每天的摘要只会留在站内、不会主动找你。" +
             (f"\n\n{context}" if context else ""))
    with st.expander("📲 现在绑定（扫码，30 秒完成）", expanded=True):
        render(user_id, key_prefix=key_prefix)
    return False

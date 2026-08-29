# -*- coding: utf-8 -*-
"""
收盘摘要展示层（读 data/digest/latest.md + history/）

门禁设计（与全站门禁原则一致）：
  **摘要内容本身开放**——它是最好的引流物，看得懂就会想每天都收到；
  **「送到你手上」才是付费项**——推送依赖用户邮箱与我们每天的持续投入，
  这一段依旧锁在 VIP 后面。锁内容会把人赶走，锁投递才是可持续的。
"""
from __future__ import annotations

import json
import os

import streamlit as st

import auth

DIGEST_DIR = os.path.join("data", "digest")
LATEST_MD = os.path.join(DIGEST_DIR, "latest.md")
LATEST_JSON = os.path.join(DIGEST_DIR, "latest.json")
HIST_DIR = os.path.join(DIGEST_DIR, "history")


@st.cache_data(ttl=600)
def _read_text(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


@st.cache_data(ttl=600)
def _read_meta() -> dict | None:
    if not os.path.exists(LATEST_JSON):
        return None
    try:
        with open(LATEST_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@st.cache_data(ttl=600)
def _history_dates() -> list[str]:
    if not os.path.isdir(HIST_DIR):
        return []
    out = []
    for fn in os.listdir(HIST_DIR):
        if fn.endswith(".md") and fn[:-3].isdigit():
            out.append(fn[:-3])
    return sorted(out, reverse=True)


def _render_subscribe() -> None:
    st.markdown("---")
    st.markdown("### 📮 每天收盘后自动送到你手上")
    st.caption(
        "摘要内容在这里始终免费可看。付费部分是**投递**：收盘后自动推送到邮箱，"
        "并且带上「你的池子今日」这一段——用你自己的自选股算的，别人的摘要里没有这段。"
    )

    if not auth.is_logged_in():
        st.info("🔐 登录后可开启推送订阅。")
        if st.button("前往登录 / 注册", key="digest_login", use_container_width=False):
            st.switch_page("pages/auth.py")
        return

    if not auth.is_vip():
        st.warning(
            "👑 推送订阅为 VIP 权益。开通后：\n\n"
            "- 每个交易日收盘后自动收到摘要邮件\n"
            "- 摘要顶部附「你的池子今日」个性化段落（中位涨幅、最强最弱、命中榜单）\n"
            "- 明日验证条件逐条带今日基准值，第二天可对账"
        )
        if st.button("👑 查看开通方式", key="digest_vip", use_container_width=False):
            st.switch_page("pages/dashboard.py")
        return

    days = auth.get_vip_remaining_days()
    st.success(
        f"✅ 推送已对你的账号生效（VIP 剩余 {days} 天）。"
        if days is not None else "✅ 推送已对你的账号生效。"
    )
    st.caption(
        "推送地址即账号邮箱。个性化段落取自「我的池子」页面里保存的自选股——"
        "自选为空时该段不出现（不会用占位内容凑数）。"
    )


def render_digest_page() -> None:
    st.header("📮 收盘摘要")
    st.caption("每个交易日收盘后自动生成 | 只播报派生结论与可对账条件，不重复各家 app 都有的原始数据")

    meta = _read_meta()
    md = _read_text(LATEST_MD)

    if not md:
        st.info(
            "⏳ 收盘摘要尚未生成。它依赖当日的情绪派生指标与榜单产出，"
            "首次跑批（`daily_digest.py`）后开始每日累积。"
        )
        _render_subscribe()
        return

    if meta:
        c1, c2, c3 = st.columns(3)
        c1.metric("统计日", meta.get("date", "—"))
        c2.metric("生成时间", (meta.get("generated_at") or "—")[-8:])
        c3.metric("数据缺失项", len(meta.get("missing") or []))
        if meta.get("missing"):
            with st.expander(f"⚠️ 本次有 {len(meta['missing'])} 项数据未取到（不是「无事发生」）"):
                for m in meta["missing"]:
                    st.markdown(f"- {m}")

    hist = _history_dates()
    view_md = md
    view_date = (meta or {}).get("date", "latest")
    if hist:
        opts = ["最新"] + hist
        pick = st.selectbox("查看日期", opts, index=0,
                            help="历史摘要原样保存，不做事后修改——这样「明日验证条件」才能被回头核对。")
        if pick != "最新":
            t = _read_text(os.path.join(HIST_DIR, f"{pick}.md"))
            if t:
                view_md, view_date = t, pick

    with st.container(border=True):
        st.markdown(view_md)

    st.download_button(
        "📄 下载本篇摘要 (Markdown)",
        data=view_md.encode("utf-8"),
        file_name=f"chilam_digest_{view_date}.md",
        mime="text/markdown",
    )

    _render_subscribe()

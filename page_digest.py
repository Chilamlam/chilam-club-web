# -*- coding: utf-8 -*-
"""
收盘摘要展示层（最新正文与推送共用生成器；历史原文读 history/）

门禁设计（与全站门禁原则一致）：
  **摘要内容本身开放**——它是最好的引流物，看得懂就会想每天都收到；
  **「送到你手上」才是付费项**——推送依赖用户绑定的微信与我们每天的持续投入，
  这一段依旧锁在 VIP 后面。锁内容会把人赶走，锁投递才是可持续的。

投递通道口径（2026-08-30 更正，不要再写回"邮箱"）：
  实际生效的唯一通道是**微信（WxPusher，扫码绑定）**。邮件通道的
  DIGEST_SMTP_* 并未配置，代码里那条路恒不发信。页面之前写"推送到邮箱"，
  属于承诺了一个不存在的功能——付费用户会守着邮箱等一封永远不来的信，
  而后台日志跟"这个人没订阅"完全一样，谁都发现不了。
  文案只允许承诺已经跑通的通道。
  （2026-09-23 补充）SMTP 那五个 DIGEST_SMTP_* 键现在**不再是一条死路**：
  密码找回也复用同一套凭据（唯一真源收口在 mailer.py），站点一旦配置，
  摘要与重置码都会真的发出去。所以这里的口径从"绝对不发邮件"改成
  "主通道是微信，开通邮件通道后未绑微信的用户改收邮件版"——
  两句话都随配置状态成立，不会再出现"文案说没有、功能其实有"的反向矛盾。
  另注意区分**两条独立通道**：摘要投递 ≠ 密码找回；重置码走微信或注册邮箱，
  与订阅状态无关，别在这里把两者混着说。
"""
from __future__ import annotations

import json
import os

import streamlit as st

import auth
import push_binding as pb
import narrative
import digest
import review_language

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
        "摘要内容在这里始终免费可看。付费部分是**投递**：收盘后自动推送到你的微信，"
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
            "- 每个交易日收盘后，当日摘要自动推到你的微信\n"
            "- 摘要顶部附「你的池子今日」个性化段落（中位涨幅、最强最弱、命中榜单）\n"
            "- 市场跨日变化、题材强弱和接力样本统一汇总"
        )
        if st.button("👑 查看开通方式", key="digest_vip", use_container_width=False):
            st.switch_page("pages/dashboard.py")
        return

    days = auth.get_vip_remaining_days()
    st.success(
        f"✅ 推送权益已对你的账号生效（VIP 剩余 {days} 天）。"
        if days is not None else "✅ 推送权益已对你的账号生效。"
    )

    # 权益生效 ≠ 能收到。绑定是投递的唯一开关，必须在这里当场把它做完，
    # 否则用户看到上面那个绿色的"已生效"就走了，然后每天等一条不会来的消息。
    user = auth.get_current_user() or {}
    uid = user.get("user_id")
    if uid is not None:
        pb.render_gate(
            uid, key_prefix="digest",
            context="绑定后今晚收盘就能收到第一条；不绑定的话，摘要仍然每天更新在本页，"
                    "只是不会主动找你。")
    st.caption(
        "投递通道是微信（扫码绑定一次即可）。个性化段落取自「我的池子」页面里保存的自选股——"
        "自选为空时该段不出现（不会用占位内容凑数）。站点开通邮件通道后，"
        "未绑微信的订阅用户会改收到邮件版摘要。"
    )


def render_digest_page() -> None:
    st.header("📮 收盘摘要")
    st.caption("市场较昨日的变化 · 当日领涨题材 · 近期强势方向的内部表现")
    hist = _history_dates()
    pick = st.selectbox("查看日期", ["最新"] + hist, index=0,
                        help="历史记录保留当时的文字和数据；最新复盘使用当前生成规则。")
    if pick == "最新":
        # 同一生成器服务网页与后续推送；旧 markdown 不会继续覆盖修正后的口径。
        current = digest.build_markdown()
        view_md, view_date = current["markdown"], current.get("date") or "latest"
        story = current["narrative"]
        st.subheader("🧭 今日复盘")
        st.caption(f"统计日：{narrative._date_display(story.get('date'))} · 数据生成：{current.get('generated_at') or '未确认'}")
        if story.get("markdown"):
            st.markdown(story["markdown"])
        else:
            st.info("市场复盘数据暂未齐备。")
        if story.get("relay_markdown"):
            with st.expander("短线接力样本", expanded=False):
                st.markdown(story["relay_markdown"])
        if current.get("detail_markdown"):
            with st.expander("战法池、行业信号与榜单明细", expanded=False):
                st.markdown(current["detail_markdown"])
        if current["missing"]:
            with st.expander(f"数据说明（{len(current['missing'])}项缺失）", expanded=False):
                for item in current["missing"]:
                    st.caption(item)
    else:
        archived_md = _read_text(os.path.join(HIST_DIR, f"{pick}.md"))
        view_date = pick
        if not archived_md:
            st.info("该日归档暂不可用。")
            return
        # 原始 Markdown 继续留在 history/ 供追溯；页面只改写过时的判断性
        # 表述，历史日期、数字和当时实际生成的结构保持不变。
        view_md = review_language.current_wording(archived_md)
        st.caption("历史归档：日期与数字保留；判断性表述已按当前中性口径改写。")
        st.markdown(view_md)

    st.download_button(
        "📄 下载本篇摘要 (Markdown)",
        data=view_md.encode("utf-8"),
        file_name=f"chilam_digest_{view_date}.md",
        mime="text/markdown",
    )

    _render_subscribe()

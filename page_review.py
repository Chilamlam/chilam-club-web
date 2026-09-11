# -*- coding: utf-8 -*-
"""
引导式复盘答卷页面

流程：填卷（选择题）→ 交卷（锁定）→ 解锁 AI 答卷对比 → 次日出回验。

门禁分层（谁管什么）：
  - 未登录 → 跳登录页（页面层，与站内其他 VIP/登录门禁一致）
  - AI 答卷解锁 → **database.get_ai_review 在后端拒绝**，本页只渲染
    后端给的结果。绝不把 AI 答卷数据发给未交卷用户的浏览器。
  - 付费墙 → 填卷免费；AI 对比当日可看，**历史对比与命中率统计是 VIP**。
    与站内「内容免费、投递收费」同一哲学：养成习惯免费，深度价值收费。

Streamlit 约定（与 page_digest 等页面一致）：
  - 多页路由只认 app.py + pages/；本文件由 app.py 侧边栏路由调用 render_review_page()
  - 页首 is_logged_in 校验才是真门禁，模块 import 不设防
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import streamlit as st

import auth
import database
from review_template import DEFAULT_TEMPLATE, get_checkable_ids
import review_evidence

AI_DIR = os.path.join("data", "review", "ai_answers")

# 当日板块涨幅榜（最强题材题的选项来源）——优先读跑批产物，
# 读不到时该题降级为「无法判断」而不是编一个选项列表。
SECTOR_PATH = os.path.join("data", "sector_rotation", "analysis.json")

_CST = timezone(timedelta(hours=8))


def _current_trade_date() -> str:
    """答卷针对的交易日（CST 口径，与跑批锚定同套规则）。
    错误防线：datetime 与 timezone 都从模块顶层导入，不做函数内
    重新 import——Streamlit Cloud 重载时偶有局部导入失败归到下一行 st.markdown
    报错，把 import 挪到顶部能彻底消除这类错觉归因。"""
    now = datetime.now(_CST)
    if now.weekday() == 5:
        return (now - timedelta(days=1)).strftime("%Y%m%d")
    if now.weekday() == 6:
        return (now - timedelta(days=2)).strftime("%Y%m%d")
    # 19:30 前（数据未就位）→ 昨日复盘；19:30 后 → 今日
    if now.hour < 19 or (now.hour == 19 and now.minute < 30):
        return (now - timedelta(days=1)).strftime("%Y%m%d")
    return now.strftime("%Y%m%d")


def _load_local_ai(trade_date: str) -> dict | None:
    """本地 AI 答卷产物（开发态预览用）。**生产数据源是 Supabase**
    （database.get_ai_review，带后端解锁门禁），不要把这函数接进
    页面对比流程——本地文件没有门禁，接了等于绕过后端解锁判定。"""
    p = os.path.join(AI_DIR, f"{trade_date}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _sector_options() -> list[str]:
    """当日板块涨幅榜 Top10 → 最强题材题选项。"""
    if not os.path.exists(SECTOR_PATH):
        return []
    try:
        with open(SECTOR_PATH, "r", encoding="utf-8") as f:
            j = json.load(f)
        return [t.get("name", "") for t in (j.get("top") or [])[:10] if t.get("name")]
    except Exception:
        return []


def _render_evidence(qid: str, evidence: dict) -> None:
    """单题的当日标的/数据参考（expander，默认收起）。
    素材来自 review_evidence（计算层，只给事实名单/数字，不给站内定性
    判读——那是考题本身，亮出来等于泄题）。缺数据时静默跳过，不占位。"""
    ev = evidence.get(qid)
    if not ev:
        return
    with st.expander(f"📌 当日参考 · {ev['title']}", expanded=False):
        for line in ev.get("lines") or []:
            st.markdown(f"<div style='font-size:0.86em'>{line}</div>",
                        unsafe_allow_html=True)
        st.caption(f"来源：{ev.get('source', '')}")


def _render_form(trade_date: str, existing: dict | None) -> None:
    """填卷表单（草稿可反复保存，交卷后锁定）。"""
    saved = (existing or {}).get("answers") or {}
    if isinstance(saved, str):
        try:
            saved = json.loads(saved)
        except Exception:
            saved = {}
    saved_plan = (existing or {}).get("plan_text") or ""

    sector_opts = _sector_options()
    # 每题标的参考一次构建，循环内复用（2026-09-11 用户反馈：题目下
    # 要有对应标的选择参考——纯选择题没有盘面锚点，退化成闭眼猜）
    evidence = review_evidence.build_evidence(trade_date)
    with st.form("review_form"):
        st.caption("草稿自动随「保存」更新；**交卷后锁定**，AI 答卷随即解锁。")
        answers: dict[str, str] = {}
        sections = []
        for q in DEFAULT_TEMPLATE:
            if q["section"] not in sections:
                sections.append(q["section"])
        for sec in sections:
            st.markdown(f"#### {sec}")
            for q in DEFAULT_TEMPLATE:
                if q["section"] != sec:
                    continue
                qid, qtype = q["id"], q["type"]
                prev = saved.get(qid, "")
                _render_evidence(qid, evidence)
                if qtype == "single" or qtype == "yn":
                    opts = q["options"]
                    idx = opts.index(prev) if prev in opts else None
                    answers[qid] = st.radio(
                        q["prompt"], opts, index=idx, key=f"rv_{qid}",
                        label_visibility="visible")
                elif qtype == "multi":
                    chosen = prev.split("、") if prev and "、" in prev else (
                        [prev] if prev else [])
                    chosen = [c for c in chosen if c in q["options"]]
                    sel = st.multiselect(
                        q["prompt"], q["options"], default=chosen, key=f"rv_{qid}")
                    answers[qid] = "、".join(sel)
                elif qtype == "choice_from_data":
                    if sector_opts:
                        idx = sector_opts.index(prev) if prev in sector_opts else None
                        answers[qid] = st.selectbox(
                            q["prompt"] + "（选项来自当日板块涨幅榜）",
                            sector_opts, index=idx, key=f"rv_{qid}")
                    else:
                        answers[qid] = st.text_input(
                            q["prompt"] + "（当日板块数据未就位，手动填写）",
                            value=prev, key=f"rv_{qid}")
                elif qtype == "text":
                    answers[qid] = st.text_area(
                        q["prompt"], value=saved_plan if qid == "q15_tomorrow_plan" else prev,
                        height=120, key=f"rv_{qid}")
        col_save, col_submit = st.columns(2)
        do_save = col_save.form_submit_button("💾 保存草稿", use_container_width=True)
        do_submit = col_submit.form_submit_button("✅ 交卷（锁定）", type="primary",
                                                  use_container_width=True)
        if do_save or do_submit:
            user = auth.get_current_user() or {}
            uid = user.get("user_id")
            plan = answers.get("q15_tomorrow_plan", "")
            row, err = database.upsert_review_answer(uid, trade_date, answers, plan)
            if err:
                if err.get("code") == "LOCKED":
                    st.warning("该日答卷已交卷锁定，不能再修改。")
                else:
                    st.error(f"保存失败：{err.get('message', '未知错误')}")
                st.rerun()
            elif do_submit:
                row, err = database.submit_review(uid, trade_date)
                if err:
                    st.error(f"交卷失败：{err.get('message', '未知错误')}")
                else:
                    st.success("已交卷 ✅ AI 答卷对比已解锁，往下看。")
                    st.rerun()
            else:
                st.toast("草稿已保存")


def _render_compare(trade_date: str, user_row: dict) -> None:
    """AI 答卷对比（已交卷用户专属）。"""
    st.subheader("🤖 AI 答卷对比")
    uid = (auth.get_current_user() or {}).get("user_id")
    ai_row, err = database.get_ai_review(trade_date, uid)
    if err:
        if err.get("code") == "NO_AI":
            st.info("⏳ 当日 AI 答卷尚未生成（通常 19:35 前后随跑批产出）。"
                    "你的答卷已锁定，生成后自动出现在这里。")
        elif err.get("code") == "TABLE_MISSING":
            st.warning("答卷系统初始化中（管理员执行 init_review_tables.sql 后可用）。")
        else:
            st.error(f"AI 答卷暂不可用：{err.get('message', '未知')}")
        return
    ai_answers = ai_row.get("answers") or {}
    if isinstance(ai_answers, str):
        try:
            ai_answers = json.loads(ai_answers)
        except Exception:
            ai_answers = {}
    user_answers = user_row.get("answers") or {}
    if isinstance(user_answers, str):
        try:
            user_answers = json.loads(user_answers)
        except Exception:
            user_answers = {}
    checkable = set(get_checkable_ids())
    # 对比视图同样展示当日参考（复盘回看：当时看到的盘面素材是什么）
    evidence = review_evidence.build_evidence(trade_date)

    same, diff = 0, 0
    for q in DEFAULT_TEMPLATE:
        qid = q["id"]
        u = str(user_answers.get(qid, "—"))
        a = ai_answers.get(qid) or {}
        a_choice = a.get("choice", "未答")
        a_basis = a.get("basis", "")
        is_same = u == a_choice
        if qid in checkable and u != "—":
            same, diff = (same + 1, diff) if is_same else (same, diff + 1)
        icon = "🟢" if is_same else ("🟡" if u == "—" or a_choice == "未答" else "🔵")
        with st.expander(f"{icon} {q['prompt']}　你：{u} ｜ AI：{a_choice}",
                         expanded=not is_same):
            _render_evidence(qid, evidence)
            if a_basis:
                st.caption(f"AI 依据：{a_basis}")
            if not is_same and qid in checkable:
                st.caption("此题可回验——次日跑批后，实际盘面会给你们各自打分。")
    if diff + same:
        st.metric("今日一致率（可回验题）", f"{same}/{same + diff}")
    if ai_row.get("plan_text"):
        st.markdown("**AI 明日预期**（结构描述，不构成投资建议）：")
        st.info(ai_row["plan_text"])


def _render_scoring(trade_date: str, user_id: int) -> None:
    """回验与命中率（VIP）。"""
    st.subheader("📊 回验与命中率")
    if not auth.is_vip():
        st.markdown(
            "复盘的长期价值在积累：**历史对比回看 + 你与 AI 的逐日命中率统计**是 VIP 权益。\n\n"
            "当日对比免费——先把每天填卷的习惯养成，命中率统计等你用得顺了再开。"
        )
        if st.button("👑 查看开通方式", key="review_vip"):
            st.switch_page("pages/dashboard.py")
        return
    rows = database.get_user_scoring(user_id, trade_date)
    if not rows:
        st.info("该日回验尚未生成（次日晚间跑批后出具）。")
        return
    u_hit = sum(1 for r in rows if r.get("user_hit"))
    a_hit = sum(1 for r in rows if r.get("ai_hit"))
    c1, c2, c3 = st.columns(3)
    c1.metric("你的命中", f"{u_hit}/{len(rows)}")
    c2.metric("AI 命中", f"{a_hit}/{len(rows)}")
    c3.metric("你 - AI", f"{u_hit - a_hit:+d}")
    for r in rows:
        mark = "✅" if r.get("user_hit") else "❌"
        ai_mark = "✅" if r.get("ai_hit") else "❌"
        st.markdown(
            f"{mark} **{r.get('question_id')}** 你：{r.get('user_choice')} "
            f"（AI {ai_mark} {r.get('ai_choice')}）→ 实际：{r.get('actual')}")
    stats = database.get_scoring_stats(user_id)
    if stats.get("total"):
        st.caption(
            f"近 {stats['days']} 个交易日累计：你 {stats['user_hits']}/{stats['total']}，"
            f"AI {stats['ai_hits']}/{stats['total']}")


def render_review_page() -> None:
    st.header("📝 引导式复盘答卷")
    st.caption("按短线全覆盖复盘流程逐题作答 → 交卷解锁 AI 答卷对比 → 次日盘面回验双方命中")

    if not auth.is_logged_in():
        st.info("🔐 登录后开始你的每日复盘（填卷免费）。")
        if st.button("前往登录 / 注册", key="review_login"):
            st.switch_page("pages/auth.py")
        return

    try:
        _render_after_login()
    except Exception as exc:
        # 调试期：把真实异常显示给用户，避免 Streamlit 把 AttributeError
        # 归到下一个 st.markdown 看不到真错点。Phase 2 上线时改成静默或
        # 写日志。修法见 page_review.py 顶部 2026-09-10 的修复说明。
        import traceback
        st.error(f"答卷页内部错误：{type(exc).__name__}: {exc}")
        with st.expander("栈详情", expanded=False):
            st.code(traceback.format_exc())


def _render_after_login() -> None:
    user = auth.get_current_user() or {}
    uid = user.get("user_id")
    trade_date = _current_trade_date()
    d = trade_date
    date_disp = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    st.markdown(f"**复盘对象：{date_disp} 的盘面**（19:35 后切换为当日）")

    existing = database.get_user_review(uid, trade_date)
    if existing and existing.get("submitted"):
        st.success(f"✅ {date_disp} 已交卷（锁定）。")
        _render_compare(trade_date, existing)
        _render_scoring(trade_date, uid)
    else:
        _render_form(trade_date, existing)

"""Small, explicit hand-offs between existing pages; no network on import."""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from navigation import NAV_GROUPS
from update_schedule import beijing_now

QUOTE_PAGE = "⚡ 实时行情 + 技术分析"
WATCH_PAGE = "⭐ 我的池子 (每日复盘)"
REVIEW_PAGE = "📝 复盘答卷"
FIB_PAGE = "📏 黄金分割参考位"
WORKBENCH_PAGE = "👁 观察记录"


def explicit_symbol(raw) -> str:
    """Preserve exchange identity; bare codes remain bare for the resolver."""
    if raw is None or (not isinstance(raw, (dict, list, tuple)) and pd.isna(raw)):
        return ""
    text = str(raw).strip()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", text, re.I)
    if match:
        return match[2].lower() + match[1]
    match = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", text, re.I)
    if match:
        return match[1].lower() + match[2]
    return text


def equity_symbol(raw) -> str:
    """Use only for known A-share source rows, never ambiguous user input."""
    code = explicit_symbol(raw)
    if not re.fullmatch(r"\d{6}", code):
        return code
    market = "bj" if code.startswith(("43", "83", "87", "88", "92")) else ("sh" if code.startswith(("6", "9")) else "sz")
    return market + code


def apply_handoff(state) -> None:
    """Run before sidebar widgets are created, including on a cross-group jump."""
    handoff = state.pop("journey_target", None)
    if not isinstance(handoff, dict):
        return
    from navigation import PAGE_ALIASES
    page = PAGE_ALIASES.get(handoff.get("page"), handoff.get("page"))
    group = next((g for g, (_, pages) in NAV_GROUPS.items() if page in pages), None)
    if group is None:
        return
    state["nav_group"] = group
    state["active_page"] = page
    state[f"nav_page_{group}"] = page
    state.setdefault("nav_saved_pages", {})[group] = page
    symbol = explicit_symbol(handoff.get("symbol"))
    if symbol:
        state["active_symbol"] = symbol
        state["fib_symbol"] = symbol
        if page == WORKBENCH_PAGE:
            state["workbench_symbol"] = symbol
        context = handoff.get("context")
        if isinstance(context, dict):
            state["workbench_prefill"] = dict(context)
        if page == QUOTE_PAGE:
            # A ranking is a daily snapshot; open the daily chart first.
            state["period_A_SHARE"] = "日K"
            state["period_BJ_SHARE"] = "日K"


def go(page: str, symbol="", *, context: dict | None = None) -> None:
    payload = {"page": page, "symbol": explicit_symbol(symbol)}
    if isinstance(context, dict):
        payload["context"] = context
    st.session_state["journey_target"] = payload
    st.rerun()


def page_link(label: str, page: str, *, key: str) -> None:
    if st.button(label, key=key, use_container_width=True):
        go(page)


def candidate_actions(frame: pd.DataFrame, *, key: str, code_col="ts_code", name_col="name", equities=False) -> None:
    """Carry exactly the selected listing's exchange and code into other tools."""
    if frame is None or frame.empty or code_col not in frame:
        return
    choices = {}
    for row in frame.to_dict("records"):
        raw = row.get(code_col)
        if pd.isna(raw) or not str(raw).strip():
            continue
        code = equity_symbol(raw) if equities else explicit_symbol(raw)
        name = row.get(name_col)
        name = "" if pd.isna(name) else str(name)
        choices[code] = f"{name} · {code}"
    if not choices:
        return
    with st.container(border=True):
        left, middle, right = st.columns([3, 1, 1])
        selected = left.selectbox("选一只继续研究", list(choices),
                                  format_func=choices.get, key=f"{key}_pick")
        if middle.button("查看技术图 →", key=f"{key}_chart", use_container_width=True):
            go(QUOTE_PAGE, selected)
        if right.button("打开观察记录 →", key=f"{key}_track", use_container_width=True):
            go(WORKBENCH_PAGE, selected)
        st.caption("技术页可核对分时与日线；观察记录会保留入选来源、风险和后续价格变化。")


def _plans_key() -> str:
    import auth
    uid = auth.get_user_id() if auth.is_logged_in() else "guest"
    return f"observation_plans_{uid}"


def plan_text(plans: list[dict]) -> str:
    lines = []
    for plan in plans:
        lines.append(f"【{plan.get('name', '')} {plan['symbol']}】{plan.get('updated_at', '')}")
        for field, label in (("basis", "观察依据"), ("trigger", "参与条件"),
                             ("invalid", "失效条件"), ("horizon", "观察周期"),
                             ("result", "实际结果/复盘")):
            if plan.get(field):
                lines.append(f"{label}：{plan[field]}")
        lines.append("")
    return "\n".join(lines).strip()


def take_review_plan(state, owner: str, existing_text: str = "") -> tuple[str, bool]:
    """Consume a transfer once and append it without replacing an existing draft."""
    transfer = state.pop("observation_review_text", None)
    if not isinstance(transfer, dict) or transfer.get("owner") != owner:
        return existing_text, False
    text = str(transfer.get("text") or "").strip()
    if not text or text in existing_text:
        return existing_text, False
    return "\n\n".join(part for part in (existing_text.strip(), text) if part), True


def render_plan_book(*, key: str) -> None:
    plans = list(st.session_state.get(_plans_key(), {}).values())
    if not plans:
        return
    with st.expander(f"本次会话的观察计划（{len(plans)}只）", expanded=False):
        text = plan_text(plans)
        st.text(text)
        st.caption("草稿保存在本次会话；下载可留档。带入复盘后，点击答卷中的“保存草稿”才会保存到账号。")
        st.download_button("下载观察计划", text, file_name="observation-plans.txt",
                           mime="text/plain", key=f"{key}_download")
        if st.button("带入复盘答卷 →", key=f"{key}_review"):
            st.session_state["observation_review_text"] = {"owner": _plans_key(), "text": text}
            go(REVIEW_PAGE)


def render_symbol_workspace(symbol: str, name: str = "", *, key: str = "symbol") -> None:
    import auth
    import database

    symbol = explicit_symbol(symbol)
    with st.expander("记录观察条件 / 加入自选", expanded=False):
        st.caption("把看图后的判断写成可检查的条件；价格、题材或时间条件不成立时，记录为什么放弃。")
        plans = st.session_state.setdefault(_plans_key(), {})
        saved = plans.get(symbol, {})
        ident = f"{key}_{_plans_key()}_{symbol}"
        with st.form(f"{ident}_plan"):
            basis = st.text_input("观察依据", value=saved.get("basis", ""),
                                  placeholder="例如：同题材领涨、回到前高附近；写清数据日期", key=f"{ident}_basis")
            trigger = st.text_input("参与条件", value=saved.get("trigger", ""),
                                    placeholder="例如：回踩某价位后重新站回；填你认可的条件", key=f"{ident}_trigger")
            invalid = st.text_input("失效条件", value=saved.get("invalid", ""),
                                    placeholder="例如：跌破参考低点、题材走弱或超过观察期限", key=f"{ident}_invalid")
            horizon = st.text_input("观察周期", value=saved.get("horizon", ""),
                                    placeholder="例如：明日盘中 / 未来三个交易日", key=f"{ident}_horizon")
            result = st.text_area("实际结果 / 复盘", value=saved.get("result", ""),
                                  placeholder="之后回来记录：触发了吗、是否执行、原判断哪里有偏差", key=f"{ident}_result")
            if st.form_submit_button("保存到本次会话"):
                if not basis.strip() or not trigger.strip() or not invalid.strip():
                    st.warning("请写明观察依据、参与条件和失效条件。")
                else:
                    plans[symbol] = dict(symbol=symbol, name=name, basis=basis.strip(),
                                         trigger=trigger.strip(), invalid=invalid.strip(),
                                         horizon=horizon.strip(), result=result.strip(),
                                         updated_at=beijing_now().strftime("%Y-%m-%d %H:%M"))
                    st.success("已保存本次会话的草稿，可在下方下载或带入复盘。")
        if re.fullmatch(r"(?:sh|sz|bj)\d{6}", symbol):
            c1, c2 = st.columns(2)
            if c1.button("加入我的池子", key=f"{ident}_watch"):
                if not auth.is_logged_in():
                    st.info("登录后可保存到账号；本次会话的观察计划仍可下载。")
                else:
                    uid = auth.get_user_id()
                    current = database.get_user_watchlist(uid)
                    equivalents = {explicit_symbol(v).lower() for v in current}
                    if symbol.lower() in equivalents:
                        st.info("该标的已在自选池中。")
                    else:
                        stored_code = f"{symbol[2:]}.{symbol[:2].upper()}"
                        updated = current + [stored_code]
                        if database.update_user_watchlist(uid, updated):
                            st.session_state["user_watchlist"] = updated
                            st.session_state["user_watchlist_owner"] = uid
                            st.success("已保存到账号的自选池。")
                        else:
                            st.error("云端保存失败，请重试；尚未加入自选。")
            if c2.button("黄金分割参考位 →", key=f"{ident}_fib"):
                go(FIB_PAGE, symbol)
        page_link("查看我的池子 →", WATCH_PAGE, key=f"{ident}_pool")
        if st.button("打开观察记录 →", key=f"{ident}_workbench"):
            go(WORKBENCH_PAGE, symbol)
    render_plan_book(key=f"{key}_plans")

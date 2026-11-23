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
WORKBENCH_PAGE = "📒 研究档案"


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
        if right.button("打开研究档案 →", key=f"{key}_track", use_container_width=True):
            go(WORKBENCH_PAGE, selected)
        st.caption("技术页可核对分时与日线；研究档案会保留入选来源、风险和后续价格变化。")


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
    """Legacy entry now points to the same durable stock dossier."""
    import research_store
    research_store.adopt_legacy_drafts()
    records = research_store.load()
    if records:
        st.caption(f"已记录 {len(records)} 条研究，可在研究档案继续核对条件与结果。")


def render_symbol_workspace(symbol: str, name: str = "", *, key: str = "symbol") -> None:
    import decision_workbench as engine
    import research_store
    import research_ui
    import research_workspace
    from site_data import load_json

    code = engine.symbol_id(symbol)
    if not code:
        return
    research_store.adopt_legacy_drafts()
    records = research_store.load()
    matches = research_workspace.active_by_symbol(list(records.values())).get(code, [])
    with st.expander("研究条件与跟踪", expanded=False):
        st.caption("与研究档案共用同一份记录；登录后保存修改会自动同步。")
        research_ui.storage_status(records)
        if matches:
            active = [r for r in matches if r["outcome"]["status"] not in ("已结束", "已放弃")]
            options = {r["id"]: r for r in active or matches}
            selected = st.selectbox("继续哪条研究", list(options), key=f"{research_store.key()}_{key}_entry",
                format_func=lambda i:f"{_playbook_label(options[i])} · {options[i]['plan']['asof']}")
            research_ui.editor(options[selected], prefix=key)
            if st.button("打开完整研究档案 →", key=f"{research_store.key()}_{key}_full"):
                st.session_state[research_store.key()+"_selected_record"] = selected
                go(WORKBENCH_PAGE, code)
        else:
            from strategy_playbooks import PLAYBOOKS
            style = st.selectbox("研究方式", list(PLAYBOOKS), format_func=lambda s:PLAYBOOKS[s]["name"], key=f"{research_store.key()}_{key}_style")
            if st.button("加入关注并记录条件", type="primary", key=f"{research_store.key()}_{key}_follow"):
                date = engine.norm_date(load_json("data/evening_plan/latest.json").get("date")) or beijing_now().strftime("%Y%m%d")
                candidate = dict(symbol=code, name=name or code, industry="", date=date,
                    reasons=["从技术图加入关注，个人依据待补充"], sources=["技术图"], risks=[])
                saved, _, err = research_store.follow(candidate, style)
                st.session_state[research_store.key()+"_selected_record"] = saved["id"]
                st.session_state[research_store.key()+"_flash"] = (bool(err), research_store.save_message(saved,err))
                go(WORKBENCH_PAGE,code)


def _playbook_label(record):
    from strategy_playbooks import PLAYBOOKS
    return PLAYBOOKS[record["plan"]["style"]]["name"] + " · " + record["plan"]["mode"]

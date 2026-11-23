"""Shared shell for the standalone Streamlit account pages.

The main site renders ``navigation.py`` itself. Streamlit also exposes every
file under ``pages/`` through its generated sidebar, so these pages need a
small shell of their own: hide that generated navigation and render the same
account actions on every standalone page.
"""
from __future__ import annotations

from typing import Literal

import streamlit as st

import auth
import feedback_ui

ShellPage = Literal["dashboard", "auth", "admin", "feedback_admin"]

MARKET_TARGET = "app.py"
DASHBOARD_TARGET = "pages/dashboard.py"
AUTH_TARGET = "pages/auth.py"
ADMIN_TARGET = "pages/admin.py"
FEEDBACK_ADMIN_TARGET = "pages/feedback_admin.py"

FEEDBACK_PAGE_LABELS = {
    "auth": "🔐 登录 / 注册",
    "dashboard": "👑 会员中心",
    "admin": "⚙️ 后台管理",
    "feedback_admin": "💬 反馈收件箱",
}

SHELL_CSS = """
<style>
  [data-testid="stSidebarNav"],
  [data-testid="stSidebarNavItems"] {
    display: none !important;
  }
  [data-testid="stAppDeployButton"],
  [data-testid="stMainMenu"] {
    display: none !important;
  }
  [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: 0.3rem;
  }
  [data-testid="stSidebar"] button {
    min-height: 2.25rem;
    text-align: left;
  }
  [data-testid="stSidebar"] button > div {
    justify-content: flex-start !important;
  }
</style>
"""


def navigation_items(
    *, logged_in: bool, is_admin: bool, current: ShellPage
) -> list[tuple[str, str, str]]:
    """Return visible sidebar destinations for the current identity.

    The admin destination is deliberately absent for both guests and ordinary
    users. The page itself still performs its own authorization check.
    """
    items = [("market", "🌐 返回市场", MARKET_TARGET)]
    if not logged_in:
        items.append(("auth", "🔐 登录 / 注册", AUTH_TARGET))
        return items

    items.extend(
        [
            ("dashboard", "👑 会员中心", DASHBOARD_TARGET),
            ("auth", "🔑 账号", AUTH_TARGET),
        ]
    )
    if is_admin:
        items.extend(
            [
                ("admin", "⚙️ 后台管理", ADMIN_TARGET),
                ("feedback_admin", "💬 反馈收件箱", FEEDBACK_ADMIN_TARGET),
            ]
        )
    return [(key, label + ("（当前）" if key == current else ""), target)
            for key, label, target in items]


def configure_page(
    *,
    current: ShellPage,
    page_title: str,
    page_icon: str,
    layout: Literal["centered", "wide"] = "centered",
) -> None:
    """Configure a standalone account page and render its shared sidebar."""
    st.set_page_config(
        page_title=page_title,
        page_icon=page_icon,
        layout=layout,
        initial_sidebar_state="expanded",
    )
    st.markdown(SHELL_CSS, unsafe_allow_html=True)
    _render_sidebar(current)


def _render_sidebar(current: ShellPage) -> None:
    try:
        logged_in = bool(auth.is_logged_in())
    except Exception:
        logged_in = False
    try:
        is_admin = logged_in and bool(auth.is_admin())
    except Exception:
        is_admin = False
    try:
        is_vip = logged_in and bool(auth.is_vip())
    except Exception:
        is_vip = False

    try:
        user = auth.get_current_user() if logged_in else None
    except Exception:
        user = None
    email = str((user or {}).get("email") or "")

    with st.sidebar:
        st.markdown("### Chilam.Club")
        st.caption("账户与站点")
        if logged_in:
            role = "管理员" if is_admin else "VIP" if is_vip else "普通用户"
            st.caption(f"👤 `{email or '已登录账号'}` · {role}")
        else:
            st.caption("👤 游客未登录")

        for key, label, target in navigation_items(
            logged_in=logged_in, is_admin=is_admin, current=current
        ):
            if key == current:
                st.button(
                    label,
                    key=f"account_shell_current_{current}",
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                )
                continue
            if st.button(
                label,
                key=f"account_shell_go_{key}",
                use_container_width=True,
            ):
                st.switch_page(target)

        feedback_ui.render_sidebar_feedback(
            FEEDBACK_PAGE_LABELS.get(current, "账户与站点")
        )

        if logged_in:
            st.markdown("---")
            if st.button(
                "🚪 退出登录",
                key="account_shell_logout",
                use_container_width=True,
            ):
                feedback_ui.request_feedback_session_clear()
                auth.logout()
                st.rerun()

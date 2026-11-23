"""Product feedback form and admin inbox."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
from uuid import uuid4

import streamlit as st

import auth
import database


CATEGORIES = database.SITE_FEEDBACK_CATEGORIES
MIN_MESSAGE = database.SITE_FEEDBACK_MIN_MESSAGE
MAX_MESSAGE = database.SITE_FEEDBACK_MAX_MESSAGE

_SESSION_KEY = "_site_feedback_session_key"
_SUBMITTED_KEY = "_site_feedback_submitted"
_FALLBACK_KEY = "_site_feedback_fallback_text"
_IDENTITY_KEY = "_site_feedback_identity"
_RESET_PENDING_KEY = "_site_feedback_reset_pending"
_CATEGORY_INPUT_KEY = "_site_feedback_category_input"
_MESSAGE_INPUT_KEY = "_site_feedback_message_input"
_CONTACT_INPUT_KEY = "_site_feedback_contact_input"
_FORM_KEY = "site_feedback_form"
_ADMIN_FLASH_KEY = "_site_feedback_admin_flash"
_ADMIN_STATUS_KEY = "site_feedback_admin_status"
_ADMIN_PAGE_KEY = "site_feedback_admin_page"
_ADMIN_FILTER_KEY = "site_feedback_admin_filter"
_ADMIN_PAGE_SIZE = 25
_SQL_PATH = Path(__file__).resolve().with_name("init_site_feedback.sql")
_RATE_SALT_ENV = "FEEDBACK_RATE_SALT"
_RATE_SALT_MIN_LENGTH = 32
_VISITOR_HASH_CONTEXT = "chilam-feedback-visitor-v1:"


def _china_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _display_time(value=None) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone(timedelta(hours=8)))
            return parsed.strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return str(value)[:16].replace("T", " ")
    return _china_now().strftime("%Y-%m-%d %H:%M")


def _stable_feedback_rate_salt() -> str:
    """Return a persistent HMAC salt without logging or exposing it.

    FEEDBACK_RATE_SALT is preferred. A configured JWT secret is an acceptable
    fallback because it is already required to persist across deployments.
    An ephemeral JWT secret is deliberately rejected: changing the salt would
    reset every anonymous visitor's quota after a process restart.
    """
    candidates = [os.environ.get(_RATE_SALT_ENV, "")]
    try:
        if hasattr(st, "secrets"):
            candidates.append(str(st.secrets.get(_RATE_SALT_ENV, "") or ""))
    except Exception:
        pass

    source = str(getattr(auth, "JWT_SECRET_SOURCE", "") or "")
    if (
        source
        and source != "ephemeral-random"
        and not source.startswith(getattr(auth, "REJECTED_PREFIX", "\0"))
    ):
        candidates.append(str(getattr(auth, "JWT_SECRET", "") or ""))

    for candidate in candidates:
        salt = str(candidate or "").strip()
        if len(salt.encode("utf-8")) >= _RATE_SALT_MIN_LENGTH:
            return salt
    return ""


def _current_ip_address():
    try:
        return st.context.ip_address
    except Exception:
        return None


def _build_visitor_key(ip_address, salt) -> str:
    """Normalize an IP in memory and return only its keyed SHA-256 digest."""
    salt = str(salt or "")
    if len(salt.encode("utf-8")) < _RATE_SALT_MIN_LENGTH:
        return ""
    try:
        parsed_ip = ipaddress.ip_address(str(ip_address or "").strip())
        if isinstance(parsed_ip, ipaddress.IPv6Address) and parsed_ip.ipv4_mapped:
            parsed_ip = parsed_ip.ipv4_mapped
        canonical_ip = str(parsed_ip)
    except ValueError:
        return ""
    message = (_VISITOR_HASH_CONTEXT + canonical_ip).encode("utf-8")
    return hmac.new(
        salt.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def _anonymous_visitor_key() -> str:
    return _build_visitor_key(
        _current_ip_address(),
        _stable_feedback_rate_salt(),
    )


def _identity_error() -> dict:
    return {
        "code": "RATE_IDENTITY_UNAVAILABLE",
        "message": (
            "暂时无法确认反馈访客身份。请登录后提交，"
            "或复制下方内容交给管理员。"
        ),
    }


def _session_key() -> str:
    key = str(st.session_state.get(_SESSION_KEY) or "").strip()
    if not key:
        key = uuid4().hex
        st.session_state[_SESSION_KEY] = key
    return key


def clear_feedback_session() -> None:
    """Remove all identity-scoped feedback state from the Streamlit session."""
    for key in (
        _SESSION_KEY,
        _SUBMITTED_KEY,
        _FALLBACK_KEY,
        _IDENTITY_KEY,
        _RESET_PENDING_KEY,
        _CATEGORY_INPUT_KEY,
        _MESSAGE_INPUT_KEY,
        _CONTACT_INPUT_KEY,
        _ADMIN_FLASH_KEY,
        _ADMIN_STATUS_KEY,
        _ADMIN_PAGE_KEY,
        _ADMIN_FILTER_KEY,
    ):
        st.session_state.pop(key, None)


def request_feedback_session_clear() -> None:
    """Clear non-widget data now and defer widget-key cleanup to safe pre-render.

    Streamlit forbids deleting a keyed widget's state after that widget was
    created in the same run. Logout/login handlers call this immediately; the
    next feedback render consumes the pending flag before widgets exist.
    """
    for key in (
        _SESSION_KEY,
        _SUBMITTED_KEY,
        _FALLBACK_KEY,
        _IDENTITY_KEY,
        _ADMIN_FLASH_KEY,
        _ADMIN_STATUS_KEY,
        _ADMIN_PAGE_KEY,
        _ADMIN_FILTER_KEY,
    ):
        st.session_state.pop(key, None)
    st.session_state[_RESET_PENDING_KEY] = True


def _sync_feedback_identity(user_id=None) -> None:
    identity = (
        f"account:{int(user_id)}"
        if user_id not in (None, "")
        else "guest"
    )
    previous = st.session_state.get(_IDENTITY_KEY)
    has_feedback_state = any(
        key in st.session_state
        for key in (
            _SESSION_KEY,
            _SUBMITTED_KEY,
            _FALLBACK_KEY,
            _CATEGORY_INPUT_KEY,
            _MESSAGE_INPUT_KEY,
            _CONTACT_INPUT_KEY,
        )
    )
    if previous is not None and previous != identity:
        clear_feedback_session()
    elif previous is None and has_feedback_state:
        # Legacy/session migration: never carry a draft across an unknown identity.
        clear_feedback_session()
    st.session_state[_IDENTITY_KEY] = identity


def _current_section() -> str:
    try:
        return str(st.query_params.get("section", "") or "").strip()
    except Exception:
        return ""


def build_feedback_text(
    *,
    category: str,
    message: str,
    page: str,
    section: str = "",
    contact: str = "",
    user_id=None,
    created_at: str = "",
) -> str:
    """Build the copyable fallback record used when cloud storage is unavailable."""
    lines = [
        "Chilam Club 产品反馈",
        f"时间：{_display_time(created_at)}",
        f"分类：{str(category or '').strip()}",
        f"页面：{str(page or '').strip() or '未记录'}",
    ]
    if str(section or "").strip():
        lines.append(f"子页面：{str(section).strip()}")
    if user_id not in (None, ""):
        lines.append(f"账号编号：{user_id}")
    if str(contact or "").strip():
        lines.append(f"联系方式：{str(contact).strip()}")
    lines.extend(["", "说明：", str(message or "").strip()])
    return "\n".join(lines)


def _receipt(success, duplicate=False) -> None:
    if duplicate:
        st.success("本次会话已经提交过反馈，无需重复提交。")
    else:
        feedback_id = (success or {}).get("id")
        created_at = (success or {}).get("created_at")
        label = f"已收到反馈 #{feedback_id}" if feedback_id else "已收到反馈"
        st.success(label)
        st.caption(f"提交时间：{_display_time(created_at)}")
    st.caption("一个浏览器会话只提交一次；如需继续补充，请记录本次反馈编号后联系管理员。")


def render_feedback_form(page: str, section: str = "") -> None:
    """Render the guest-accessible feedback form.

    On success, session state hides the form for the rest of this Streamlit
    session. On storage failure, the complete record is exposed in ``st.code``
    so the user can copy it rather than losing what they typed.
    """
    if st.session_state.pop(_RESET_PENDING_KEY, False):
        clear_feedback_session()

    identity_err = None
    try:
        logged_in = bool(auth.is_logged_in())
    except Exception:
        logged_in = False
        identity_err = _identity_error()
    user_id = None
    if logged_in:
        try:
            user_id = int(auth.get_user_id())
            if user_id <= 0:
                raise ValueError("invalid user id")
        except Exception:
            user_id = None
            identity_err = _identity_error()
    _sync_feedback_identity(user_id)

    submitted = st.session_state.get(_SUBMITTED_KEY)
    if isinstance(submitted, dict):
        _receipt(
            submitted.get("row"),
            duplicate=bool(submitted.get("duplicate")),
        )
        return

    st.text(f"当前页面：{page or '未记录'}")
    if str(section or "").strip():
        st.text(f"子页面：{str(section).strip()}")
    st.caption(
        "反馈会记录页面、时间和分类"
        + ("，并关联内部账号编号" if user_id not in (None, "") else "")
        + "；不会自动保存你的邮箱。"
    )

    with st.form(_FORM_KEY, clear_on_submit=False):
        category = st.selectbox(
            "反馈分类",
            CATEGORIES,
            key=_CATEGORY_INPUT_KEY,
        )
        message = st.text_area(
            "具体说明",
            placeholder=f"至少 {MIN_MESSAGE} 个字，最多 {MAX_MESSAGE} 个字",
            max_chars=MAX_MESSAGE,
            key=_MESSAGE_INPUT_KEY,
        )
        contact = st.text_input(
            "联系方式（选填）",
            placeholder="微信、邮箱或其他方便联系的方式",
            max_chars=200,
            key=_CONTACT_INPUT_KEY,
        )
        do_submit = st.form_submit_button("提交反馈", type="primary")

    if not do_submit:
        fallback = str(st.session_state.get(_FALLBACK_KEY) or "")
        if fallback:
            st.warning("反馈尚未写入云端，请复制下面的文本后交给管理员。")
            st.code(fallback, language=None)
        return

    message_clean = str(message or "").strip()
    if len(message_clean) < MIN_MESSAGE:
        st.warning(f"请把情况写具体一些，至少 {MIN_MESSAGE} 个字。")
        return

    visitor_key = None
    if identity_err:
        row, err = None, identity_err
    else:
        if user_id is None:
            visitor_key = _anonymous_visitor_key()
            if not visitor_key:
                row, err = None, _identity_error()
            else:
                try:
                    row, err = database.create_site_feedback(
                        category=category,
                        message=message_clean,
                        page=page,
                        section=section,
                        contact=contact,
                        user_id=None,
                        visitor_key=visitor_key,
                        session_key=_session_key(),
                    )
                except Exception:
                    row, err = None, {
                        "code": "FEEDBACK_WRITE_FAILED",
                        "message": (
                            "反馈服务暂时不可用，请复制下方内容交给管理员。"
                        ),
                    }
        else:
            try:
                row, err = database.create_site_feedback(
                    category=category,
                    message=message_clean,
                    page=page,
                    section=section,
                    contact=contact,
                    user_id=user_id,
                    visitor_key=None,
                    session_key=_session_key(),
                )
            except Exception:
                row, err = None, {
                    "code": "FEEDBACK_WRITE_FAILED",
                    "message": "反馈服务暂时不可用，请复制下方内容交给管理员。",
                }
    if row:
        st.session_state[_SUBMITTED_KEY] = {"row": row, "duplicate": False}
        st.session_state.pop(_FALLBACK_KEY, None)
        st.rerun()

    duplicate = str((err or {}).get("code") or "").upper() in {
        "23505",
        "UNIQUE_VIOLATION",
    }
    if duplicate:
        st.session_state[_SUBMITTED_KEY] = {"row": None, "duplicate": True}
        st.rerun()

    fallback = build_feedback_text(
        category=category,
        message=message_clean,
        page=page,
        section=section,
        contact=contact,
        user_id=user_id,
    )
    st.session_state[_FALLBACK_KEY] = fallback
    detail = str((err or {}).get("message") or "数据库暂时不可用").strip()
    st.error(f"反馈没有写入云端：{detail}")
    st.caption("下面保留了完整内容。请复制后交给管理员，避免刚才填写的内容丢失。")
    st.code(fallback, language=None)


def render_sidebar_feedback(page: str) -> None:
    """Top-level sidebar entry, kept separate from the account expander."""
    submitted = isinstance(st.session_state.get(_SUBMITTED_KEY), dict)
    with st.expander("💬 产品反馈", expanded=submitted):
        render_feedback_form(page, _current_section())


def _table_missing(err: dict) -> bool:
    return (
        str((err or {}).get("code") or "").upper() == "PGRST205"
        or (err or {}).get("status") == 404
    )


def _reset_admin_page() -> None:
    st.session_state[_ADMIN_PAGE_KEY] = 1


def render_admin_feedback(handled_by: str = "") -> None:
    """Render one server-filtered feedback page and a handled action."""
    flash = st.session_state.pop(_ADMIN_FLASH_KEY, None)
    if flash:
        kind, text = flash
        {"ok": st.success, "err": st.error}.get(kind, st.info)(text)

    status_label = st.selectbox(
        "反馈状态",
        ("待处理", "已处理", "全部"),
        key=_ADMIN_STATUS_KEY,
        on_change=_reset_admin_page,
    )
    status_code = {
        "待处理": "new",
        "已处理": "handled",
        "全部": None,
    }[status_label]
    try:
        page_number = max(1, int(st.session_state.get(_ADMIN_PAGE_KEY, 1)))
    except (TypeError, ValueError):
        page_number = 1
    st.session_state[_ADMIN_PAGE_KEY] = page_number
    offset = (page_number - 1) * _ADMIN_PAGE_SIZE

    rows, err = database.get_site_feedback(
        status=status_code,
        limit=_ADMIN_PAGE_SIZE + 1,
        offset=offset,
    )
    if err:
        if _table_missing(err):
            st.warning(
                "⚠️ `site_feedback` 表尚未创建，用户提交时会看到可复制的兜底文本。"
                "请在 Supabase SQL Editor 执行下面的幂等建表脚本。"
            )
            if _SQL_PATH.exists():
                st.code(_SQL_PATH.read_text(encoding="utf-8"), language="sql")
            else:
                st.error("未找到 init_site_feedback.sql")
        else:
            st.error(
                "读取反馈失败（HTTP "
                f"{(err or {}).get('status')}）："
                f"{(err or {}).get('message') or (err or {}).get('detail') or '未知原因'}"
            )
            st.caption("这是取数失败，不是“没有反馈”；请稍后重试。")
        return

    if not rows and page_number > 1:
        st.session_state[_ADMIN_PAGE_KEY] = page_number - 1
        st.rerun()
    has_next = len(rows) > _ADMIN_PAGE_SIZE
    visible = rows[:_ADMIN_PAGE_SIZE]
    st.caption(
        f"{status_label} · 第 {page_number} 页 · 每页最多 {_ADMIN_PAGE_SIZE} 条"
    )

    if not visible:
        st.info(
            "当前没有反馈。"
            if status_label == "全部"
            else f"当前没有{status_label}反馈。"
        )
        return

    for row in visible:
        feedback_id = row.get("id")
        status = str(row.get("status") or "new")
        created_text = (
            _display_time(row.get("created_at"))
            if row.get("created_at")
            else "时间未记录"
        )
        with st.container(border=True):
            st.markdown(
                f"**#{feedback_id} · {row.get('category') or '未分类'}** · "
                f"{created_text} · "
                f"{'已处理' if status == 'handled' else '待处理'}"
            )
            row_page = str(row.get("page") or "未记录")
            section = str(row.get("section") or "").strip()
            st.text(f"页面：{row_page}" + (f" / {section}" if section else ""))
            st.text(str(row.get("message") or ""))
            contact = str(row.get("contact") or "").strip()
            if contact:
                st.caption("联系方式")
                st.text(contact)
            if row.get("user_id") not in (None, ""):
                st.caption(f"账号编号：{row.get('user_id')}")
            if status == "handled":
                handled_at = (
                    _display_time(row.get("handled_at"))
                    if row.get("handled_at")
                    else "时间未记录"
                )
                handled_by_text = str(row.get("handled_by") or "管理员")
                st.text(f"由 {handled_by_text} 于 {handled_at} 标记为已处理。")
            elif st.button(
                "标记已处理",
                key=f"site_feedback_handle_{feedback_id}",
                type="primary",
            ):
                ok, mark_err = database.mark_site_feedback_handled(
                    feedback_id, handled_by=handled_by
                )
                if ok:
                    st.session_state[_ADMIN_FLASH_KEY] = (
                        "ok",
                        f"反馈 #{feedback_id} 已标记为处理完成。",
                    )
                else:
                    st.session_state[_ADMIN_FLASH_KEY] = (
                        "err",
                        "标记失败："
                        + str((mark_err or {}).get("message") or "未知原因"),
                    )
                st.rerun()

    nav_prev, nav_next = st.columns(2)
    with nav_prev:
        if st.button(
            "上一页",
            key="site_feedback_prev_page",
            disabled=page_number <= 1,
            width="stretch",
        ):
            st.session_state[_ADMIN_PAGE_KEY] = page_number - 1
            st.rerun()
    with nav_next:
        if st.button(
            "下一页",
            key="site_feedback_next_page",
            disabled=not has_next,
            width="stretch",
        ):
            st.session_state[_ADMIN_PAGE_KEY] = page_number + 1
            st.rerun()

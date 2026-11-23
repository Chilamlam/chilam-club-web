import json
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import database
import feedback_ui


VISITOR_KEY = "a" * 64


IDENTITY_SYNC_PROBE = """
import streamlit as st
import feedback_ui

choice = st.selectbox(
    "identity",
    ["guest", "account:1", "account:2"],
    key="probe_identity",
)
user_id = None if choice == "guest" else int(choice.split(":")[1])
feedback_ui._sync_feedback_identity(user_id)
"""

REQUEST_CLEAR_PROBE = """
import streamlit as st
import feedback_ui

if st.button("request-clear", key="probe_request_clear"):
    feedback_ui.request_feedback_session_clear()
    st.session_state["probe_requested"] = True

if st.button("consume-clear", key="probe_consume_clear"):
    if st.session_state.get(feedback_ui._RESET_PENDING_KEY):
        feedback_ui.clear_feedback_session()
        st.session_state["probe_consumed"] = True
"""


def feedback_probe():
    import feedback_ui

    feedback_ui.render_feedback_form(
        page="👁 观察记录",
        section="已结束",
    )


def feedback_admin_probe():
    import feedback_ui

    feedback_ui.render_admin_feedback(handled_by="admin@example.com")


def _all_rendered_text(app):
    values = []
    for group in (
        app.text,
        app.caption,
        app.markdown,
        app.code,
        app.error,
        app.warning,
        app.success,
        app.info,
    ):
        values.extend(str(item.value) for item in group)
    return "\n".join(values)


def _seed_feedback_state(app, identity):
    app.session_state[feedback_ui._IDENTITY_KEY] = identity
    app.session_state[feedback_ui._SESSION_KEY] = "old-session"
    app.session_state[feedback_ui._SUBMITTED_KEY] = {"row": {"id": 99}}
    app.session_state[feedback_ui._FALLBACK_KEY] = "old fallback"
    app.session_state[feedback_ui._CATEGORY_INPUT_KEY] = "页面报错"
    app.session_state[feedback_ui._MESSAGE_INPUT_KEY] = "old draft"
    app.session_state[feedback_ui._CONTACT_INPUT_KEY] = "old contact"
    app.session_state[feedback_ui._ADMIN_FLASH_KEY] = ("ok", "old flash")
    app.session_state[feedback_ui._ADMIN_STATUS_KEY] = "已处理"
    app.session_state[feedback_ui._ADMIN_PAGE_KEY] = 4
    app.session_state[feedback_ui._ADMIN_FILTER_KEY] = "old filter"


class FeedbackDatabaseTests(unittest.TestCase):
    def test_authenticated_feedback_sends_user_id_without_visitor_key(self):
        row = {
            "id": 7,
            "created_at": "2026-11-23T01:00:00+00:00",
            "category": "页面报错",
        }
        with patch.object(
            database, "_supabase_request", return_value=([row], None)
        ) as request:
            saved, err = database.create_site_feedback(
                category="页面报错",
                message="点击观察记录后页面没有更新",
                page="👁 观察记录",
                section="已结束",
                contact="wx: test",
                user_id=12,
                visitor_key=VISITOR_KEY,
                session_key="session-1",
            )

        self.assertIsNone(err)
        self.assertEqual(saved, row)
        args, kwargs = request.call_args
        self.assertEqual(args[:2], ("POST", "site_feedback"))
        payload = kwargs["json_data"]
        self.assertEqual(payload["page"], "👁 观察记录")
        self.assertEqual(payload["section"], "已结束")
        self.assertEqual(payload["user_id"], 12)
        self.assertNotIn("visitor_key", payload)
        self.assertEqual(payload["session_key"], "session-1")
        self.assertTrue(kwargs["return_error"])

    def test_guest_feedback_sends_only_the_hmac_visitor_key(self):
        raw_ip = "198.51.100.9"
        visitor_key = feedback_ui._build_visitor_key(
            raw_ip, "s" * 32
        )
        with patch.object(
            database,
            "_supabase_request",
            return_value=([{"id": 8}], None),
        ) as request:
            saved, err = database.create_site_feedback(
                category="页面报错",
                message="游客反馈只保存不可逆访客摘要",
                page="全市场看板",
                visitor_key=visitor_key,
                session_key="guest-session",
            )

        self.assertEqual(saved, {"id": 8})
        self.assertIsNone(err)
        payload = request.call_args.kwargs["json_data"]
        self.assertEqual(payload["visitor_key"], visitor_key)
        self.assertNotIn(raw_ip, json.dumps(payload, ensure_ascii=False))

    def test_guest_feedback_fails_closed_without_visitor_key(self):
        with patch.object(database, "_supabase_request") as request:
            saved, err = database.create_site_feedback(
                category="页面报错",
                message="缺少稳定访客身份时不得写入数据库",
                page="全市场看板",
                session_key="guest-session",
            )
        self.assertIsNone(saved)
        self.assertEqual(err["code"], "RATE_IDENTITY_UNAVAILABLE")
        request.assert_not_called()

    def test_feedback_rejects_non_positive_user_id(self):
        with patch.object(database, "_supabase_request") as request:
            saved, err = database.create_site_feedback(
                category="页面报错",
                message="无效账号编号不能穿透到数据库",
                page="全市场看板",
                user_id=0,
                session_key="invalid-user",
            )
        self.assertIsNone(saved)
        self.assertEqual(err["code"], "INVALID_USER")
        request.assert_not_called()

    def test_create_feedback_validates_before_network(self):
        with patch.object(database, "_supabase_request") as request:
            saved, err = database.create_site_feedback(
                category="页面报错",
                message="短",
                page="全市场看板",
                session_key="session-1",
            )
        self.assertIsNone(saved)
        self.assertEqual(err["code"], "MESSAGE_TOO_SHORT")
        request.assert_not_called()

    def test_public_errors_hide_database_details(self):
        raw_error = {
            "status": 500,
            "code": "XX000",
            "message": "permission denied for table site_feedback",
            "detail": "internal schema and SQL detail",
        }
        with patch.object(
            database, "_supabase_request", return_value=(None, raw_error)
        ):
            saved, err = database.create_site_feedback(
                category="页面报错",
                message="提交后页面出现了一条服务错误",
                page="全市场看板",
                visitor_key=VISITOR_KEY,
                session_key="session-generic",
            )
        self.assertIsNone(saved)
        self.assertEqual(err["code"], "FEEDBACK_WRITE_FAILED")
        self.assertNotIn("permission", err["message"].lower())
        self.assertNotIn("schema", err["message"].lower())

        rate_cases = (
            (
                "CHILAM_FEEDBACK_VISITOR_RATE_LIMIT_10M",
                "VISITOR_RATE_LIMIT_10M",
                "当前网络",
            ),
            (
                "CHILAM_FEEDBACK_VISITOR_RATE_LIMIT_24H",
                "VISITOR_RATE_LIMIT_24H",
                "24 小时",
            ),
            (
                "CHILAM_FEEDBACK_USER_RATE_LIMIT_10M",
                "USER_RATE_LIMIT_10M",
                "当前账号",
            ),
            (
                "CHILAM_FEEDBACK_USER_RATE_LIMIT_24H",
                "USER_RATE_LIMIT_24H",
                "24 小时",
            ),
            (
                "CHILAM_FEEDBACK_ANON_SITE_RATE_LIMIT_10M",
                "ANON_SITE_RATE_LIMIT_10M",
                "匿名反馈较多",
            ),
            (
                "CHILAM_FEEDBACK_IDENTITY_UNAVAILABLE",
                "RATE_IDENTITY_UNAVAILABLE",
                "无法确认",
            ),
        )
        for marker, code, text in rate_cases:
            with self.subTest(marker=marker), patch.object(
                database,
                "_supabase_request",
                return_value=(
                    None,
                    {
                        "status": 500,
                        "code": "P0001",
                        "message": marker,
                        "detail": "raw trigger detail must stay private",
                    },
                ),
            ):
                _saved, err = database.create_site_feedback(
                    category="页面报错",
                    message="提交后页面出现了一条服务错误",
                    page="全市场看板",
                    user_id=12 if "USER_" in code else None,
                    visitor_key=(
                        None if "USER_" in code else VISITOR_KEY
                    ),
                    session_key=f"session-{marker}",
                )
            self.assertEqual(err["code"], code)
            self.assertIn(text, err["message"])
            self.assertNotIn("raw trigger detail", err["message"])

        with patch.object(
            database,
            "_supabase_request",
            return_value=(None, {"code": "23505", "message": "duplicate key"}),
        ):
            _saved, err = database.create_site_feedback(
                category="页面报错",
                message="提交后页面出现了一条服务错误",
                page="全市场看板",
                visitor_key=VISITOR_KEY,
                session_key="session-duplicate",
            )
        self.assertEqual(err["code"], "23505")

    def test_feedback_list_filters_before_paging(self):
        with patch.object(
            database, "_supabase_request", return_value=([], None)
        ) as request:
            rows, err = database.get_site_feedback(
                status="new", limit=26, offset=25
            )
        self.assertEqual(rows, [])
        self.assertIsNone(err)
        params = request.call_args.kwargs["params"]
        self.assertEqual(params["status"], "eq.new")
        self.assertEqual(params["limit"], "26")
        self.assertEqual(params["offset"], "25")

    def test_feedback_list_keeps_failure_distinct_from_empty(self):
        missing = {"status": 404, "code": "PGRST205", "message": "missing"}
        with patch.object(
            database, "_supabase_request", return_value=(None, missing)
        ):
            _rows, err = database.get_site_feedback(status="new")
        self.assertEqual(err, missing)

        with patch.object(
            database, "_supabase_request", return_value=(None, None)
        ):
            rows, err = database.get_site_feedback(status="new")
        self.assertEqual(rows, [])
        self.assertEqual(err["code"], "INVALID_RESPONSE")

    def test_feedback_list_rejects_unknown_status_before_network(self):
        with patch.object(database, "_supabase_request") as request:
            rows, err = database.get_site_feedback(status="deleted")
        self.assertEqual(rows, [])
        self.assertEqual(err["code"], "INVALID_STATUS")
        request.assert_not_called()

    def test_mark_handled_is_idempotent_after_concurrent_update(self):
        with patch.object(
            database,
            "_supabase_request",
            side_effect=[
                ([], None),
                ([{"id": 8, "status": "handled"}], None),
            ],
        ) as request:
            ok, err = database.mark_site_feedback_handled(
                8, "admin@example.com"
            )
        self.assertTrue(ok)
        self.assertIsNone(err)
        patch_args = request.call_args_list[0]
        self.assertEqual(
            patch_args.args[:2],
            ("PATCH", "site_feedback?status=eq.new&id=eq.8"),
        )
        self.assertEqual(
            request.call_args_list[1].args[:2], ("GET", "site_feedback")
        )

    def test_mark_handled_reports_missing_or_unknown_rows(self):
        with patch.object(
            database,
            "_supabase_request",
            side_effect=[([], None), ([], None)],
        ):
            ok, err = database.mark_site_feedback_handled(8, "admin")
        self.assertFalse(ok)
        self.assertEqual(err["code"], "NO_ROW")

        with patch.object(
            database,
            "_supabase_request",
            side_effect=[
                ([], None),
                (None, {"status": 503, "message": "offline"}),
            ],
        ):
            ok, err = database.mark_site_feedback_handled(8, "admin")
        self.assertFalse(ok)
        self.assertEqual(err["code"], "STATE_UNKNOWN")

    def test_admin_verification_selects_only_required_user_fields(self):
        with patch.object(
            database,
            "_supabase_request",
            return_value=(
                [{"id": 9, "email": "a@example.com", "is_admin": True}],
                None,
            ),
        ) as request:
            user, err = database.get_user_by_id_checked(9)
        self.assertIsNone(err)
        self.assertEqual(user["id"], 9)
        params = request.call_args.kwargs["params"]
        self.assertEqual(params["select"], "id,email,is_admin")


class FeedbackVisitorIdentityTests(unittest.TestCase):
    def test_same_ip_and_salt_are_stable_across_sessions(self):
        salt = "s" * 32
        first = feedback_ui._build_visitor_key("203.0.113.7", salt)
        second = feedback_ui._build_visitor_key("203.0.113.7", salt)
        other = feedback_ui._build_visitor_key("203.0.113.8", salt)
        other_salt = feedback_ui._build_visitor_key("203.0.113.7", "x" * 32)

        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertNotEqual(first, other_salt)
        self.assertNotIn("203.0.113.7", first)

    def test_ipv6_is_canonicalized_before_hashing(self):
        salt = "s" * 32
        expanded = feedback_ui._build_visitor_key("2001:0db8::1", salt)
        compressed = feedback_ui._build_visitor_key("2001:db8::1", salt)
        self.assertEqual(expanded, compressed)
        native_v4 = feedback_ui._build_visitor_key("203.0.113.7", salt)
        mapped_v4 = feedback_ui._build_visitor_key(
            "::ffff:203.0.113.7", salt
        )
        self.assertEqual(native_v4, mapped_v4)

    def test_rate_salt_priority_and_ephemeral_rejection(self):
        with patch.dict(
            os.environ,
            {"FEEDBACK_RATE_SALT": "e" * 32},
            clear=False,
        ), patch.object(
            feedback_ui.st,
            "secrets",
            {},
        ), patch.object(
            feedback_ui.auth,
            "JWT_SECRET_SOURCE",
            "env:JWT_SECRET",
        ), patch.object(
            feedback_ui.auth,
            "JWT_SECRET",
            "j" * 32,
        ):
            self.assertEqual(
                feedback_ui._stable_feedback_rate_salt(), "e" * 32
            )

        with patch.dict(
            os.environ,
            {"FEEDBACK_RATE_SALT": ""},
            clear=False,
        ), patch.object(
            feedback_ui.st,
            "secrets",
            {},
        ), patch.object(
            feedback_ui.auth,
            "JWT_SECRET_SOURCE",
            "env:JWT_SECRET",
        ), patch.object(
            feedback_ui.auth,
            "JWT_SECRET",
            "j" * 32,
        ):
            self.assertEqual(
                feedback_ui._stable_feedback_rate_salt(), "j" * 32
            )

        with patch.dict(
            os.environ,
            {"FEEDBACK_RATE_SALT": ""},
            clear=False,
        ), patch.object(
            feedback_ui.st,
            "secrets",
            {"FEEDBACK_RATE_SALT": "c" * 32},
        ), patch.object(
            feedback_ui.auth,
            "JWT_SECRET_SOURCE",
            "ephemeral-random",
        ):
            self.assertEqual(
                feedback_ui._stable_feedback_rate_salt(), "c" * 32
            )

        with patch.dict(
            os.environ,
            {"FEEDBACK_RATE_SALT": ""},
            clear=False,
        ), patch.object(
            feedback_ui.st,
            "secrets",
            {},
        ), patch.object(
            feedback_ui.auth,
            "JWT_SECRET_SOURCE",
            "ephemeral-random",
        ), patch.object(
            feedback_ui.auth,
            "JWT_SECRET",
            "b" * 64,
        ):
            self.assertEqual(feedback_ui._stable_feedback_rate_salt(), "")


class FeedbackUiTests(unittest.TestCase):
    def test_guest_submission_records_page_and_hides_form_for_session(self):
        captured = []

        def save(**kwargs):
            captured.append(kwargs)
            return (
                {"id": 21, "created_at": "2026-11-23T01:00:00+00:00"},
                None,
            )

        with patch.object(
            feedback_ui.auth, "is_logged_in", return_value=False
        ), patch.object(
            feedback_ui,
            "_anonymous_visitor_key",
            return_value=VISITOR_KEY,
        ), patch.object(
            feedback_ui.database, "create_site_feedback", side_effect=save
        ):
            app = AppTest.from_function(feedback_probe).run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            app.text_area[0].set_value("观察记录切换后内容没有更新")
            next(
                button for button in app.button if button.label == "提交反馈"
            ).click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["page"], "👁 观察记录")
        self.assertEqual(captured[0]["section"], "已结束")
        self.assertIsNone(captured[0]["user_id"])
        self.assertEqual(captured[0]["visitor_key"], VISITOR_KEY)
        self.assertIn("已收到反馈 #21", [item.value for item in app.success])
        self.assertNotIn("提交反馈", [button.label for button in app.button])

    def test_public_storage_error_never_echoes_database_detail(self):
        raw = "permission denied for schema private with internal SQL"
        error = {
            "status": 500,
            "code": "XX000",
            "message": raw,
            "detail": raw,
        }
        with patch.object(
            feedback_ui.auth, "is_logged_in", return_value=False
        ), patch.object(
            feedback_ui,
            "_anonymous_visitor_key",
            return_value=VISITOR_KEY,
        ), patch.object(
            feedback_ui.database,
            "_supabase_request",
            return_value=(None, error),
        ):
            app = AppTest.from_function(feedback_probe).run(timeout=30)
            app.text_area[0].set_value("这个页面点击后没有任何反应")
            next(
                button for button in app.button if button.label == "提交反馈"
            ).click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        rendered = _all_rendered_text(app)
        self.assertNotIn("permission denied", rendered)
        self.assertNotIn("internal SQL", rendered)
        self.assertIn("反馈服务暂时不可用", rendered)
        block = "\n".join(str(item.value) for item in app.code)
        self.assertIn("页面：👁 观察记录", block)
        self.assertIn("这个页面点击后没有任何反应", block)

    def test_anonymous_rate_limit_shows_safe_retry_message(self):
        error = {
            "status": 500,
            "code": "P0001",
            "message": "CHILAM_FEEDBACK_VISITOR_RATE_LIMIT_10M",
        }
        with patch.object(
            feedback_ui.auth, "is_logged_in", return_value=False
        ), patch.object(
            feedback_ui,
            "_anonymous_visitor_key",
            return_value=VISITOR_KEY,
        ), patch.object(
            feedback_ui.database,
            "_supabase_request",
            return_value=(None, error),
        ):
            app = AppTest.from_function(feedback_probe).run(timeout=30)
            app.text_area[0].set_value("连续测试反馈接口时遇到的限流提示")
            next(
                button for button in app.button if button.label == "提交反馈"
            ).click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        rendered = _all_rendered_text(app)
        self.assertIn("当前网络提交反馈过于频繁", rendered)
        self.assertIn("10 分钟后再试", rendered)
        self.assertNotIn("CHILAM_FEEDBACK_VISITOR_RATE_LIMIT_10M", rendered)

    def test_duplicate_feedback_returns_idempotent_receipt(self):
        with patch.object(
            feedback_ui.auth, "is_logged_in", return_value=False
        ), patch.object(
            feedback_ui,
            "_anonymous_visitor_key",
            return_value=VISITOR_KEY,
        ), patch.object(
            feedback_ui.database,
            "_supabase_request",
            return_value=(None, {"code": "23505", "message": "duplicate key"}),
        ):
            app = AppTest.from_function(feedback_probe).run(timeout=30)
            app.text_area[0].set_value("本次会话中已经提交过一次产品反馈")
            next(
                button for button in app.button if button.label == "提交反馈"
            ).click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(
            any(
                "本次会话已经提交过反馈" in item.value
                for item in app.success
            )
        )
        self.assertNotIn("提交反馈", [button.label for button in app.button])

    def test_logged_in_submission_attaches_internal_user_id(self):
        captured = []

        def save(**kwargs):
            captured.append(kwargs)
            return (
                {"id": 22, "created_at": "2026-11-23T01:00:00+00:00"},
                None,
            )

        with patch.object(
            feedback_ui.auth, "is_logged_in", return_value=True
        ), patch.object(
            feedback_ui.auth, "get_user_id", return_value=42
        ), patch.object(
            feedback_ui.database, "create_site_feedback", side_effect=save
        ):
            app = AppTest.from_function(feedback_probe).run(timeout=30)
            app.text_area[0].set_value("登录用户也可以提交产品建议")
            next(
                button for button in app.button if button.label == "提交反馈"
            ).click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(captured[0]["user_id"], 42)
        self.assertIsNone(captured[0]["visitor_key"])

    def test_missing_ip_or_salt_fails_closed_with_copyable_fallback(self):
        raw_ip = "203.0.113.44"
        cases = (
            (raw_ip, "", "缺少稳定盐时不能静默降级"),
            (None, "s" * 32, "缺少连接 IP 时不能静默降级"),
        )
        for ip_address, salt, message in cases:
            with self.subTest(ip_address=ip_address, salt=salt), patch.object(
                feedback_ui.auth, "is_logged_in", return_value=False
            ), patch.object(
                feedback_ui,
                "_current_ip_address",
                return_value=ip_address,
            ), patch.object(
                feedback_ui,
                "_stable_feedback_rate_salt",
                return_value=salt,
            ), patch.object(
                feedback_ui.database, "create_site_feedback"
            ) as save:
                app = AppTest.from_function(feedback_probe).run(timeout=30)
                app.text_area[0].set_value(message)
                next(
                    button
                    for button in app.button
                    if button.label == "提交反馈"
                ).click().run(timeout=30)

            self.assertFalse(app.exception, str(app.exception))
            save.assert_not_called()
            rendered = _all_rendered_text(app)
            self.assertIn("暂时无法确认反馈访客身份", rendered)
            self.assertNotIn(raw_ip, rendered)
            session_dump = "\n".join(
                f"{key}={app.session_state[key]}"
                for key in app.session_state
            )
            self.assertNotIn(raw_ip, session_dump)
            self.assertTrue(app.code)

    def test_logged_in_identity_failure_does_not_downgrade_to_guest(self):
        with patch.object(
            feedback_ui.auth, "is_logged_in", return_value=True
        ), patch.object(
            feedback_ui.auth,
            "get_user_id",
            side_effect=RuntimeError("identity unavailable"),
        ), patch.object(
            feedback_ui.database, "create_site_feedback"
        ) as save:
            app = AppTest.from_function(feedback_probe).run(timeout=30)
            app.text_area[0].set_value("登录身份异常时不能降级为匿名配额")
            next(
                button for button in app.button if button.label == "提交反馈"
            ).click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        save.assert_not_called()
        self.assertIn(
            "暂时无法确认反馈访客身份",
            _all_rendered_text(app),
        )

    def test_different_sessions_reuse_the_same_visitor_key(self):
        captured = []

        def save(**kwargs):
            captured.append(kwargs)
            return (
                {
                    "id": len(captured),
                    "created_at": "2026-11-23T01:00:00+00:00",
                },
                None,
            )

        with patch.object(
            feedback_ui.auth, "is_logged_in", return_value=False
        ), patch.object(
            feedback_ui,
            "_anonymous_visitor_key",
            return_value=VISITOR_KEY,
        ), patch.object(
            feedback_ui.database, "create_site_feedback", side_effect=save
        ):
            for index in range(2):
                app = AppTest.from_function(feedback_probe).run(timeout=30)
                app.text_area[0].set_value(
                    f"同一访客在第 {index + 1} 个会话提交反馈"
                )
                next(
                    button
                    for button in app.button
                    if button.label == "提交反馈"
                ).click().run(timeout=30)
                self.assertFalse(app.exception, str(app.exception))

        self.assertEqual(len(captured), 2)
        self.assertEqual(
            {item["visitor_key"] for item in captured},
            {VISITOR_KEY},
        )
        self.assertNotEqual(
            captured[0]["session_key"],
            captured[1]["session_key"],
        )

    def test_different_accounts_use_their_own_user_quota(self):
        captured = []

        def save(**kwargs):
            captured.append(kwargs)
            return (
                {
                    "id": len(captured),
                    "created_at": "2026-11-23T01:00:00+00:00",
                },
                None,
            )

        for user_id in (41, 42):
            with patch.object(
                feedback_ui.auth, "is_logged_in", return_value=True
            ), patch.object(
                feedback_ui.auth,
                "get_user_id",
                return_value=user_id,
            ), patch.object(
                feedback_ui.database,
                "create_site_feedback",
                side_effect=save,
            ):
                app = AppTest.from_function(feedback_probe).run(timeout=30)
                app.text_area[0].set_value(
                    f"账号 {user_id} 使用自己的反馈配额"
                )
                next(
                    button
                    for button in app.button
                    if button.label == "提交反馈"
                ).click().run(timeout=30)
                self.assertFalse(app.exception, str(app.exception))

        self.assertEqual([item["user_id"] for item in captured], [41, 42])
        self.assertTrue(
            all(item["visitor_key"] is None for item in captured)
        )

    def test_admin_can_mark_pending_feedback_handled(self):
        row = {
            "id": 31,
            "status": "new",
            "category": "功能建议",
            "message": "希望观察记录可以按行业筛选",
            "page": "👁 观察记录",
            "section": "",
            "contact": "",
            "user_id": None,
            "created_at": "2026-11-23T01:00:00+00:00",
        }
        with patch.object(
            feedback_ui.database,
            "get_site_feedback",
            return_value=([row], None),
        ), patch.object(
            feedback_ui.database,
            "mark_site_feedback_handled",
            return_value=(True, None),
        ) as mark:
            app = AppTest.from_function(feedback_admin_probe).run(timeout=30)
            next(
                button for button in app.button if button.label == "标记已处理"
            ).click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        mark.assert_called_once_with(31, handled_by="admin@example.com")
        self.assertTrue(any("反馈 #31" in item.value for item in app.success))

    def test_admin_pagination_survives_row_named_page(self):
        row = {
            "id": 32,
            "status": "handled",
            "category": "功能建议",
            "message": "分页变量不应被页面字段覆盖",
            "page": "👁 观察记录",
            "section": "已结束",
            "contact": "",
            "user_id": None,
            "created_at": "2026-11-23T01:00:00+00:00",
        }
        with patch.object(
            feedback_ui.database,
            "get_site_feedback",
            return_value=([row], None),
        ) as get_feedback:
            app = AppTest.from_function(feedback_admin_probe).run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        get_feedback.assert_called_once_with(
            status="new", limit=26, offset=0
        )
        rendered = _all_rendered_text(app)
        self.assertIn("第 1 页", rendered)
        self.assertIn("页面：👁 观察记录 / 已结束", rendered)
        self.assertIn("上一页", [button.label for button in app.button])
        self.assertIn("下一页", [button.label for button in app.button])

    def test_admin_renders_user_content_as_plain_text(self):
        malicious = "[点我](https://evil.example)"
        image = "![图](https://evil.example/x.png)"
        row = {
            "id": 33,
            "status": "new",
            "category": "其他",
            "message": malicious,
            "page": image,
            "section": malicious,
            "contact": image,
            "user_id": None,
            "created_at": "2026-11-23T01:00:00+00:00",
        }
        with patch.object(
            feedback_ui.database,
            "get_site_feedback",
            return_value=([row], None),
        ):
            app = AppTest.from_function(feedback_admin_probe).run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        plain_text = "\n".join(str(item.value) for item in app.text)
        markdown = "\n".join(str(item.value) for item in app.markdown)
        self.assertIn(malicious, plain_text)
        self.assertIn(image, plain_text)
        self.assertNotIn(malicious, markdown)
        self.assertNotIn(image, markdown)

    def test_admin_table_missing_shows_migration_sql(self):
        error = {"status": 404, "code": "PGRST205", "message": "missing"}
        with patch.object(
            feedback_ui.database,
            "get_site_feedback",
            return_value=([], error),
        ):
            app = AppTest.from_function(feedback_admin_probe).run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any("site_feedback" in item.value for item in app.warning))
        blocks = "\n".join(str(item.value) for item in app.code)
        self.assertIn("CREATE TABLE IF NOT EXISTS", blocks)
        self.assertIn("ENABLE ROW LEVEL SECURITY", blocks)


class FeedbackIdentityTests(unittest.TestCase):
    def test_guest_to_account_clears_all_feedback_state(self):
        app = AppTest.from_string(IDENTITY_SYNC_PROBE)
        _seed_feedback_state(app, "guest")
        app.run(timeout=30)
        app.selectbox[0].select("account:1").run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(
            app.session_state[feedback_ui._IDENTITY_KEY], "account:1"
        )
        self._assert_feedback_state_cleared(app)

    def test_account_switch_clears_draft_and_admin_paging(self):
        app = AppTest.from_string(IDENTITY_SYNC_PROBE)
        _seed_feedback_state(app, "account:1")
        app.session_state["probe_identity"] = "account:1"
        app.run(timeout=30)
        app.selectbox[0].select("account:2").run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(
            app.session_state[feedback_ui._IDENTITY_KEY], "account:2"
        )
        self._assert_feedback_state_cleared(app)

    def test_account_to_guest_clears_all_feedback_state(self):
        app = AppTest.from_string(IDENTITY_SYNC_PROBE)
        _seed_feedback_state(app, "account:1")
        app.session_state["probe_identity"] = "account:1"
        app.run(timeout=30)
        app.selectbox[0].select("guest").run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(app.session_state[feedback_ui._IDENTITY_KEY], "guest")
        self._assert_feedback_state_cleared(app)

    def test_requested_clear_waits_for_safe_render_boundary(self):
        app = AppTest.from_string(REQUEST_CLEAR_PROBE)
        _seed_feedback_state(app, "account:1")
        app.run(timeout=30)
        app.button[0].click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(app.session_state["probe_requested"])
        self.assertTrue(app.session_state[feedback_ui._RESET_PENDING_KEY])
        self.assertNotIn(feedback_ui._SESSION_KEY, app.session_state)
        self.assertNotIn(feedback_ui._SUBMITTED_KEY, app.session_state)
        self.assertNotIn(feedback_ui._ADMIN_PAGE_KEY, app.session_state)
        self.assertEqual(
            app.session_state[feedback_ui._MESSAGE_INPUT_KEY], "old draft"
        )

        app.button[1].click().run(timeout=30)
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(app.session_state["probe_consumed"])
        self._assert_feedback_state_cleared(app)

    def _assert_feedback_state_cleared(self, app):
        for key in (
            feedback_ui._SESSION_KEY,
            feedback_ui._SUBMITTED_KEY,
            feedback_ui._FALLBACK_KEY,
            feedback_ui._CATEGORY_INPUT_KEY,
            feedback_ui._MESSAGE_INPUT_KEY,
            feedback_ui._CONTACT_INPUT_KEY,
            feedback_ui._ADMIN_FLASH_KEY,
            feedback_ui._ADMIN_STATUS_KEY,
            feedback_ui._ADMIN_PAGE_KEY,
            feedback_ui._ADMIN_FILTER_KEY,
        ):
            self.assertNotIn(key, app.session_state)


class FeedbackAdminPageTests(unittest.TestCase):
    def _run_page(self):
        return AppTest.from_file(
            str(ROOT / "pages" / "feedback_admin.py")
        ).run(timeout=30)

    def _identity_patches(self):
        return (
            patch.object(auth, "is_logged_in", return_value=True),
            patch.object(auth, "get_user_id", return_value=9),
            patch.object(auth, "is_admin", return_value=True),
            patch.object(auth, "is_vip", return_value=True),
            patch.object(
                auth,
                "get_current_user",
                return_value={"email": "admin@example.com", "is_admin": True},
            ),
        )

    def test_feedback_is_read_only_after_database_admin_verification(self):
        events = []
        identity_patches = self._identity_patches()
        with identity_patches[0], identity_patches[1], identity_patches[2], \
             identity_patches[3], identity_patches[4], patch.object(
                 database,
                 "get_user_by_id_checked",
                 side_effect=lambda user_id: (
                     events.append(("verify", user_id))
                     or (
                         {
                             "id": 9,
                             "email": "admin@example.com",
                             "is_admin": True,
                         },
                         None,
                     )
                 ),
             ), patch.object(
                 database,
                 "get_site_feedback",
                 side_effect=lambda **kwargs: (
                     events.append(("feedback", kwargs)) or ([], None)
                 ),
             ):
            app = self._run_page()

        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(
            events,
            [
                ("verify", 9),
                ("feedback", {"status": "new", "limit": 26, "offset": 0}),
            ],
        )
        self.assertIn(
            "💬 反馈收件箱（当前）",
            [button.label for button in app.sidebar.button],
        )

    def test_database_failure_fails_closed(self):
        identity_patches = self._identity_patches()
        with identity_patches[0], identity_patches[1], identity_patches[2], \
             identity_patches[3], identity_patches[4], patch.object(
                 database,
                 "get_user_by_id_checked",
                 return_value=(None, {"code": "DB_DOWN", "message": "offline"}),
             ), patch.object(database, "get_site_feedback") as get_feedback:
            app = self._run_page()

        self.assertFalse(app.exception, str(app.exception))
        get_feedback.assert_not_called()
        self.assertTrue(
            any("无法从账号表验证" in item.value for item in app.error)
        )

    def test_demoted_or_missing_user_fails_closed(self):
        cases = [
            ({"id": 9, "email": "admin@example.com", "is_admin": False}, None),
            (
                None,
                {"code": "USER_NOT_FOUND", "message": "账号不存在"},
            ),
        ]
        for verified_user, verify_err in cases:
            with self.subTest(verified_user=verified_user, verify_err=verify_err):
                identity_patches = self._identity_patches()
                with identity_patches[0], identity_patches[1], identity_patches[2], \
                     identity_patches[3], identity_patches[4], patch.object(
                         database,
                         "get_user_by_id_checked",
                         return_value=(verified_user, verify_err),
                     ), patch.object(
                         database, "get_site_feedback"
                     ) as get_feedback:
                    app = self._run_page()

                self.assertFalse(app.exception, str(app.exception))
                get_feedback.assert_not_called()
                self.assertTrue(app.error)


class FeedbackMigrationTests(unittest.TestCase):
    def test_migration_is_idempotent_private_and_rate_limited(self):
        sql = (ROOT / "init_site_feedback.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS public.site_feedback", sql)
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS visitor_key TEXT", sql
        )
        self.assertIn("ck_site_feedback_visitor_key_format", sql)
        self.assertNotIn("ck_site_feedback_subject_identity", sql)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS", sql)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn(
            "REVOKE ALL ON TABLE public.site_feedback FROM anon, authenticated",
            sql,
        )
        self.assertIn(
            "GRANT ALL ON TABLE public.site_feedback TO service_role", sql
        )
        self.assertIn(
            "ON public.site_feedback(visitor_key, created_at DESC)",
            sql,
        )
        self.assertIn(
            "ON public.site_feedback(user_id, created_at DESC)",
            sql,
        )
        self.assertIn(
            "ON public.site_feedback(created_at DESC)\n"
            "    WHERE user_id IS NULL;",
            sql,
        )
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertIn("hashtextextended", sql)
        self.assertIn("v_now - interval '10 minutes'", sql)
        self.assertIn("v_now - interval '24 hours'", sql)
        self.assertIn("c_visitor_10m_limit CONSTANT integer := 3", sql)
        self.assertIn("c_visitor_24h_limit CONSTANT integer := 10", sql)
        self.assertIn("c_user_10m_limit CONSTANT integer := 5", sql)
        self.assertIn("c_user_24h_limit CONSTANT integer := 30", sql)
        self.assertIn("c_anon_site_10m_limit CONSTANT integer := 30", sql)
        for marker in (
            "CHILAM_FEEDBACK_VISITOR_RATE_LIMIT_10M",
            "CHILAM_FEEDBACK_VISITOR_RATE_LIMIT_24H",
            "CHILAM_FEEDBACK_USER_RATE_LIMIT_10M",
            "CHILAM_FEEDBACK_USER_RATE_LIMIT_24H",
            "CHILAM_FEEDBACK_ANON_SITE_RATE_LIMIT_10M",
            "CHILAM_FEEDBACK_IDENTITY_UNAVAILABLE",
        ):
            self.assertIn(marker, sql)
        self.assertIn(
            "DROP TRIGGER IF EXISTS trg_site_feedback_anonymous_rate_limit", sql
        )
        self.assertIn(
            "DROP FUNCTION IF EXISTS "
            "public.enforce_site_feedback_anonymous_rate_limit()",
            sql,
        )
        self.assertIn(
            "CREATE TRIGGER trg_site_feedback_rate_limit", sql
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION "
            "public.enforce_site_feedback_rate_limit()",
            sql,
        )
        self.assertNotIn("USING (true)", sql)
        self.assertNotRegex(
            sql.lower(),
            r"\b(?:client_)?ip(?:_address)?\s+(?:inet|text|varchar)",
        )

    def test_trigger_lock_order_and_subject_branches_are_explicit(self):
        sql = (ROOT / "init_site_feedback.sql").read_text(encoding="utf-8")
        user_branch = sql[
            sql.index("IF NEW.user_id IS NOT NULL THEN"):
            sql.index("IF NEW.visitor_key IS NULL")
        ]
        self.assertIn("feedback-user:", user_branch)
        self.assertIn("918273", user_branch)
        self.assertNotIn("feedback-anon-global", user_branch)
        self.assertNotIn("918271", user_branch)

        guest_after_lock = sql[
            sql.index("-- 固定锁序：先全站匿名兜底"):
        ]
        guest_lock_block = guest_after_lock[
            :guest_after_lock.index("SELECT\n        count(*) FILTER")
        ]
        self.assertLess(
            guest_lock_block.index("feedback-anon-global"),
            guest_lock_block.index("feedback-visitor:"),
        )

    def test_main_and_admin_pages_wire_the_feedback_loop(self):
        main_source = (ROOT / "app.py").read_text(encoding="utf-8")
        admin_source = (ROOT / "pages" / "admin.py").read_text(encoding="utf-8")
        inbox_source = (ROOT / "pages" / "feedback_admin.py").read_text(
            encoding="utf-8"
        )
        shell_source = (ROOT / "account_shell.py").read_text(encoding="utf-8")
        feedback_source = (ROOT / "feedback_ui.py").read_text(encoding="utf-8")

        self.assertIn("feedback_ui.render_sidebar_feedback(page)", main_source)
        self.assertIn("💬 打开反馈收件箱", admin_source)
        self.assertNotIn("render_admin_feedback(", admin_source)
        self.assertIn("feedback_ui.render_admin_feedback(", inbox_source)
        self.assertIn("feedback_ui.render_sidebar_feedback(", shell_source)
        self.assertNotIn("admin_notify", feedback_source)

    def test_main_feedback_entry_is_a_sidebar_root_item(self):
        lines = (ROOT / "app.py").read_text(encoding="utf-8").splitlines()
        account_line = next(
            line
            for line in lines
            if "with st.expander(\"账号与站点\")" in line
        )
        feedback_line = next(
            line
            for line in lines
            if "feedback_ui.render_sidebar_feedback(page)" in line
        )
        self.assertEqual(
            len(account_line) - len(account_line.lstrip()),
            len(feedback_line) - len(feedback_line.lstrip()),
        )

    def test_every_logout_path_requests_feedback_cleanup(self):
        for relative in (
            "app.py",
            "account_shell.py",
            "pages/auth.py",
            "pages/dashboard.py",
        ):
            with self.subTest(relative=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertRegex(
                    source,
                    re.compile(
                        r"feedback_ui\.request_feedback_session_clear\(\)"
                        r"\s*\n\s*auth\.logout\(\)"
                    ),
                )

    def test_main_sidebar_exposes_feedback_for_guest_and_user(self):
        identities = [
            {
                "is_logged_in": False,
                "get_user_id": None,
                "is_admin": False,
                "is_vip": False,
                "user": None,
            },
            {
                "is_logged_in": True,
                "get_user_id": 42,
                "is_admin": False,
                "is_vip": True,
                "user": {"email": "user@example.com", "is_admin": False},
            },
        ]
        for identity in identities:
            with self.subTest(logged_in=identity["is_logged_in"]), \
                 patch.object(
                     auth,
                     "is_logged_in",
                     return_value=identity["is_logged_in"],
                 ), patch.object(
                     auth,
                     "get_user_id",
                     return_value=identity["get_user_id"],
                 ), patch.object(
                     auth,
                     "is_admin",
                     return_value=identity["is_admin"],
                 ), patch.object(
                     auth,
                     "is_vip",
                     return_value=identity["is_vip"],
                 ), patch.object(
                     auth,
                     "get_current_user",
                     return_value=identity["user"],
                 ):
                app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=60)

            self.assertFalse(app.exception, str(app.exception))
            labels = [expander.label for expander in app.sidebar.expander]
            self.assertIn("账号与站点", labels)
            self.assertIn("💬 产品反馈", labels)


if __name__ == "__main__":
    sys.exit(unittest.main())

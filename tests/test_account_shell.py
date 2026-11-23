from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import account_shell


def shell_probe():
    import account_shell

    account_shell.configure_page(
        current="dashboard",
        page_title="shell test",
        page_icon="T",
        layout="centered",
    )


class AccountNavigationTests(unittest.TestCase):
    def test_admin_entry_is_absent_for_guests_and_ordinary_users(self):
        guest = account_shell.navigation_items(
            logged_in=False, is_admin=False, current="auth"
        )
        ordinary = account_shell.navigation_items(
            logged_in=True, is_admin=False, current="dashboard"
        )
        admin = account_shell.navigation_items(
            logged_in=True, is_admin=True, current="admin"
        )

        self.assertEqual([item[0] for item in guest], ["market", "auth"])
        self.assertEqual(
            [item[0] for item in ordinary], ["market", "dashboard", "auth"]
        )
        self.assertEqual(
            [item[0] for item in admin],
            ["market", "dashboard", "auth", "admin", "feedback_admin"],
        )
        self.assertTrue(admin[-2][1].endswith("（当前）"))
        self.assertEqual(admin[-1][0], "feedback_admin")

    def test_shell_css_hides_streamlit_generated_navigation(self):
        self.assertIn('[data-testid="stSidebarNav"]', account_shell.SHELL_CSS)
        self.assertIn("display: none !important", account_shell.SHELL_CSS)

    def test_ordinary_sidebar_has_no_admin_button(self):
        with patch.object(account_shell.auth, "is_logged_in", return_value=True), \
             patch.object(account_shell.auth, "is_admin", return_value=False), \
             patch.object(account_shell.auth, "is_vip", return_value=False), \
             patch.object(account_shell.auth, "get_current_user",
                          return_value={"email": "user@example.com"}):
            app = AppTest.from_function(shell_probe).run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        labels = [button.label for button in app.sidebar.button]
        self.assertIn("👑 会员中心（当前）", labels)
        self.assertIn("🔑 账号", labels)
        self.assertNotIn("⚙️ 后台管理", labels)
        self.assertNotIn("💬 反馈收件箱", labels)
        self.assertIn(
            "💬 产品反馈",
            [expander.label for expander in app.sidebar.expander],
        )

    def test_admin_sidebar_contains_admin_button(self):
        with patch.object(account_shell.auth, "is_logged_in", return_value=True), \
             patch.object(account_shell.auth, "is_admin", return_value=True), \
             patch.object(account_shell.auth, "is_vip", return_value=False), \
             patch.object(account_shell.auth, "get_current_user",
                          return_value={"email": "admin@example.com"}):
            app = AppTest.from_function(shell_probe).run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertIn(
            "⚙️ 后台管理", [button.label for button in app.sidebar.button]
        )
        self.assertIn(
            "💬 反馈收件箱", [button.label for button in app.sidebar.button]
        )
        self.assertIn(
            "💬 产品反馈",
            [expander.label for expander in app.sidebar.expander],
        )

    def test_guest_shell_contains_feedback_entry(self):
        with patch.object(account_shell.auth, "is_logged_in", return_value=False), \
             patch.object(account_shell.auth, "is_admin", return_value=False), \
             patch.object(account_shell.auth, "is_vip", return_value=False), \
             patch.object(account_shell.auth, "get_current_user", return_value=None):
            app = AppTest.from_function(shell_probe).run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        self.assertIn(
            "💬 产品反馈",
            [expander.label for expander in app.sidebar.expander],
        )

    def test_shell_logout_button_requests_feedback_cleanup(self):
        with patch.object(account_shell.auth, "is_logged_in", return_value=True), \
             patch.object(account_shell.auth, "is_admin", return_value=False), \
             patch.object(account_shell.auth, "is_vip", return_value=False), \
             patch.object(account_shell.auth, "get_current_user",
                          return_value={"email": "user@example.com"}), \
             patch.object(account_shell.feedback_ui,
                          "request_feedback_session_clear") as clear_feedback, \
             patch.object(account_shell.auth, "logout") as logout:
            app = AppTest.from_function(shell_probe).run(timeout=30)
            next(
                button for button in app.sidebar.button
                if button.label == "🚪 退出登录"
            ).click().run(timeout=30)

        self.assertFalse(app.exception, str(app.exception))
        clear_feedback.assert_called_once_with()
        logout.assert_called_once_with()


class AccountPageWiringTests(unittest.TestCase):
    def test_standalone_pages_use_the_shared_page_configuration(self):
        expected = {
            "dashboard.py": 'current="dashboard"',
            "auth.py": 'current="auth"',
            "admin.py": 'current="admin"',
            "feedback_admin.py": 'current="feedback_admin"',
        }
        for filename, marker in expected.items():
            with self.subTest(filename=filename):
                source = (ROOT / "pages" / filename).read_text(encoding="utf-8")
                self.assertIn("account_shell.configure_page(", source)
                self.assertIn(marker, source)
                self.assertNotIn("st.set_page_config(", source)


if __name__ == "__main__":
    sys.exit(unittest.main())

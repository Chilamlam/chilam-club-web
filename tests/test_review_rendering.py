import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import page_digest
import page_sentiment
import review_evidence


def digest_page():
    from page_digest import render_digest_page
    render_digest_page()


def sentiment_block():
    from page_sentiment import render_sentiment_block
    render_sentiment_block()


class ReviewRenderingTests(unittest.TestCase):
    def test_latest_shows_one_story_and_collapses_details(self):
        current = {
            "markdown": "完整下载正文", "date": "20261116", "generated_at": "19:18",
            "narrative": {"date": "20261116", "markdown": "市场事实只出现一次",
                          "relay_markdown": "接力样本明细"},
            "detail_markdown": "榜单与行业明细", "missing": [],
        }
        with patch.object(page_digest, "_history_dates", return_value=[]), \
             patch.object(page_digest.digest, "build_markdown", return_value=current), \
             patch.object(page_digest, "_read_text", return_value="资金已停止出价") as old, \
             patch.object(page_digest.auth, "is_logged_in", return_value=False):
            app = AppTest.from_function(digest_page).run()
        self.assertFalse(app.exception)
        text = "\n".join(m.value for m in app.markdown)
        self.assertEqual(text.count("市场事实只出现一次"), 1)
        self.assertNotIn("资金已停止出价", text)
        self.assertNotIn("完整下载正文", text)
        self.assertEqual(len(app.expander), 2)
        self.assertTrue(all(not e.proto.expanded for e in app.expander))
        old.assert_not_called()

    def test_history_keeps_original_and_labels_its_old_caliber(self):
        with patch.object(page_digest, "_history_dates", return_value=["20261113"]), \
             patch.object(page_digest, "_read_text", return_value="历史归档正文"), \
             patch.object(page_digest.digest, "build_markdown") as build, \
             patch.object(page_digest.auth, "is_logged_in", return_value=False):
            build.return_value = {"markdown": "最新", "date": "20261116", "narrative": {}, "missing": []}
            app = AppTest.from_function(digest_page).run()
            build.reset_mock()
            app.selectbox[0].select("20261113").run()
            build.assert_not_called()
        self.assertFalse(app.exception)
        self.assertIn("历史归档正文", [m.value for m in app.markdown])
        self.assertTrue(any("历史原文" in c.value for c in app.caption))

    def test_old_payload_text_cannot_reappear_in_homepage_or_review_evidence(self):
        derived = {
            "status": "complete", "date": "20261116", "prev_date": "20261113",
            "breadth": {"status": "ok", "up": 1136, "down": 4344, "median_pct": -2.12,
                        "mean_pct": -2.37, "divergence": -.25, "up_ratio": .207},
            "premium": {"status": "ok", "n": 6, "missing": 1, "median_pct": 5.68,
                        "win_rate": 2 / 3, "limit_again": 3},
            "phase": {"status": "ok", "phase": "发酵期", "basis": "资金已停止出价"},
            "verification_plan": [{"指标": "旧模板", "验证条件": "高度链条断裂"}],
        }
        with patch.object(page_sentiment.sm, "load_derived", return_value=derived):
            app = AppTest.from_function(sentiment_block).run()
        self.assertFalse(app.exception)
        rendered = "\n".join(m.value for m in app.markdown)
        evidence = str(review_evidence._evd_breadth(derived)) + str(review_evidence._evd_tomorrow(derived))
        for phrase in ("资金已停止出价", "高度链条断裂", "胜率"):
            self.assertNotIn(phrase, rendered + evidence)
        self.assertIn("上涨占比", evidence)
        self.assertIn("缺失行情 1 只", evidence)
        detail = next(e for e in app.expander if e.label == "统计档位与次日收盘核对")
        self.assertFalse(detail.proto.expanded)


if __name__ == "__main__":
    unittest.main()

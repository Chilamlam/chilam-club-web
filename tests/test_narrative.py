import unittest

import pandas as pd

from narrative import build_narrative
from sentiment import verification_plan


class NarrativeTests(unittest.TestCase):
    def setUp(self):
        self.derived = {
            "status": "complete", "date": "20261116", "prev_date": "20261113",
            "phase": {"status": "ok", "phase": "发酵期", "basis": "资金已停止出价"},
            "breadth": {"status": "ok", "up": 1136, "down": 4344, "median_pct": -2.12},
            "promotion": {"rates": {"1进2": {"rate": 8 / 30, "promoted": 8,
                                             "base": 30, "reliable": True}}},
            "premium": {"status": "ok", "median_pct": 5.68, "n": 6,
                        "win_rate": 2 / 3, "missing": 1},
            "ladder_gap": {"status": "ok", "max_height": 4, "total": 32, "gaps": [],
                           "verdict": "高度链条断裂"},
            "verification_plan": [{"指标": "旧文案", "验证条件": "退潮确认，资金已停止出价"}],
        }
        self.sectors = {"status": "ok", "date": "2026-11-16", "window_col": "pct_10d",
                        "universe_count": 3, "top": [
            {"code": "A", "name": "影视", "pct_10d": 9, "pct_chg": -1},
            {"code": "B", "name": "短剧", "pct_10d": 8, "pct_chg": -3},
            {"code": "C", "name": "免税", "pct_10d": 2, "pct_chg": 1},
        ]}
        self.history = pd.DataFrame([
            {"date": "2026-11-13", "code": "A", "name": "影视", "pct_chg": 2,
             "up_count": 30, "down_count": 30},
            {"date": "2026-11-16", "code": "A", "name": "影视", "pct_chg": -1,
             "up_count": 20, "down_count": 40},
            {"date": "2026-11-16", "code": "B", "name": "短剧", "pct_chg": -3},
            {"date": "2026-11-16", "code": "C", "name": "免税", "pct_chg": 1},
        ])
        self.breadth_history = pd.DataFrame([
            {"date": "20261113", "up": 765, "median_pct": -2.0},
            {"date": "20261112", "up": 600, "median_pct": -1.0},
            {"date": "20261117", "up": 5000, "median_pct": 4.0},
        ])
        self.calendar = {"start_date": "20261101", "end_date": "20261130",
                         "open_dates": ["20261112", "20261113", "20261116", "20261117"]}

    def build(self, **overrides):
        args = {"breadth_history": self.breadth_history, "sector_history": self.history,
                "calendar": self.calendar}
        args.update(overrides)
        return build_narrative(self.derived, self.sectors, {}, **args)

    def test_exact_previous_session_and_daily_leaders(self):
        result = self.build()
        md = result["markdown"]
        self.assertIn("上涨家数增加 371 家", md)
        self.assertIn("中位涨跌幅降低 0.12 个百分点", md)
        self.assertIn("2026-11-13", md)
        leaders = md.split("当日领涨题材")[1].split("。", 1)[0]
        self.assertLess(leaders.index("免税"), leaders.index("影视"))
        self.assertLess(leaders.index("影视"), leaders.index("短剧"))
        self.assertEqual(result["missing"], [])
        self.assertLess(len(md), 700)

    def test_negative_return_can_outperform_with_weak_internal_breadth(self):
        self.history.loc[self.history.code == "C", "pct_chg"] = -2
        self.sectors["top"][2]["pct_chg"] = -2
        md = self.build()["markdown"]
        self.assertIn("当日跌幅较小的题材", md)
        self.assertIn("同日题材涨跌幅中位数 -2.00%", md)
        self.assertIn("今日 -1.00%，高于题材中位数 1.00 个百分点", md)
        self.assertIn("内部 20 涨 / 40 跌", md)
        self.assertIn("上涨占比较上一交易日减少 16.7 个百分点（剔除平盘）", md)
        self.assertNotIn("走弱", md)

    def test_missing_previous_session_never_uses_older_or_future_row(self):
        history = self.breadth_history[self.breadth_history.date != "20261113"]
        result = self.build(breadth_history=history)
        self.assertNotIn("上涨家数增加", result["markdown"])
        self.assertTrue(any("2026-11-13 市场数据缺失" in m for m in result["missing"]))
        result = self.build(calendar=None)
        self.assertNotIn("较上一交易日", result["markdown"])
        self.assertTrue(any("交易日历" in m for m in result["missing"]))

    def test_mismatched_dates_incomplete_snapshot_and_batches(self):
        self.sectors["date"] = "20261113"
        result = self.build()
        self.assertNotIn("影视", result["markdown"])
        self.assertTrue(any("统计日不同" in m for m in result["missing"]))
        self.sectors["date"] = "20261116"
        result = self.build(sector_history=self.history.iloc[:2])
        self.assertNotIn("题材中位数", result["markdown"])
        self.assertNotIn("当日领涨", result["markdown"])
        self.assertTrue(any("快照不足" in m for m in result["missing"]))
        self.history.loc[(self.history.code == "A") & (self.history.date == "2026-11-16"), "pct_chg"] = 5
        result = self.build()
        film = next(line for line in result["markdown"].splitlines() if line.startswith("- **影视"))
        self.assertNotIn("内部", film)
        self.assertNotIn("高于", film)

    def test_style_and_popularity_groups_are_not_industry_themes(self):
        names = ["东方财富热股", "大盘价值", "金融地产风格", "昨日涨停", "最近多板"]
        additions = [{"date": "20261116", "code": str(i), "name": name,
                      "pct_chg": 99, "pct_10d": 99} for i, name in enumerate(names)]
        self.sectors["top"] = additions + self.sectors["top"]
        result = self.build(sector_history=pd.concat([self.history, pd.DataFrame(additions)]))
        for name in names:
            self.assertNotIn(name, result["markdown"])

    def test_invalid_numbers_are_omitted_not_zero_or_nan(self):
        for invalid in (None, float("nan"), float("inf"), True, "invalid"):
            with self.subTest(invalid=invalid):
                self.derived["breadth"]["median_pct"] = invalid
                self.derived["premium"]["median_pct"] = invalid
                result = self.build()
                self.assertNotIn("市场整体", result["markdown"])
                self.assertNotIn("中位涨跌幅", result["relay_markdown"])
                self.assertTrue(any("市场涨跌家数" in m for m in result["missing"]))

    def test_old_prose_ignored_and_sample_size_kept(self):
        result = self.build()
        text = result["markdown"] + result["relay_markdown"]
        for phrase in ("胜率", "资金已停止出价", "退潮确认", "高度链条断裂", "发酵期", "明日验证"):
            self.assertNotIn(phrase, text)
        self.assertIn("6 只", result["relay_markdown"])
        self.assertIn("上涨占比 67%", result["relay_markdown"])
        self.assertIn("1 只行情缺失", result["relay_markdown"])
        self.assertIn("8/30", result["relay_markdown"])
        self.assertNotIn("8/30", result["markdown"])
        self.derived["premium"].update(status="insufficient", n=2, missing=5)
        self.assertIn("2 只", self.build()["relay_markdown"])

    def test_height_drop_does_not_create_a_gap(self):
        self.derived["ladder_gap"]["max_height"] = 3
        result = self.build()
        self.assertIn("最高 3 板；中间高度无空档", result["relay_markdown"])
        self.derived["ladder_gap"]["gaps"] = [2]
        self.assertIn("中间缺少 [2] 板", self.build()["relay_markdown"])

    def test_next_close_comparison_is_independent_of_model_thresholds(self):
        args = [self.derived[k] for k in ("promotion", "ladder_gap", "premium", "breadth")]
        plan = verification_plan(*args)
        self.assertEqual(plan, verification_plan(*args, thresholds={"high": .9, "mid": .8, "low": .7}))
        self.assertEqual(len(plan), 4)
        for item in plan:
            self.assertIn("下一交易日收盘后", item["验证条件"])
        self.assertNotIn("资金", str(plan))
        self.assertNotIn("胜率", str(plan))
        self.assertIn("高度下降与中间缺档分别记录", str(plan))

    def test_missing_derived_does_not_invent_story(self):
        result = build_narrative(None, self.sectors, {})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["markdown"], "")


if __name__ == "__main__":
    unittest.main()

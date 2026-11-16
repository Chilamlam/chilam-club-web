import unittest

from narrative import build_narrative


class NarrativeTests(unittest.TestCase):
    def setUp(self):
        self.derived = {
            "status": "complete", "date": "20261113",
            "phase": {"status": "ok", "phase": "退潮期", "basis": "晋级率走弱"},
            "breadth": {"status": "ok", "up": 10, "down": 90, "median_pct": -2},
            "promotion": {"rates": {"1进2": {"rate": .15, "promoted": 3, "base": 20}}},
            "premium": {"status": "ok", "median_pct": 1.2, "n": 11},
            "ladder_gap": {"status": "ok", "max_height": 4, "total": 20, "verdict": "梯队连续"},
            "verification_plan": [{"指标": "晋级率", "今日基准": "15%", "验证条件": "看是否回升"}],
        }
        self.sectors = {"status": "ok", "date": "2026-11-13", "window_col": "pct_10d", "top": [
            {"name": "甲主题", "pct_10d": 10, "pct_chg": 1},
            {"name": "乙主题", "pct_10d": 8, "pct_chg": -1},
            {"name": "丙主题", "pct_10d": 7, "pct_chg": 0},
        ]}

    def test_narrative_answers_retail_questions(self):
        result = build_narrative(self.derived, self.sectors, {})
        self.assertEqual(result["status"], "ok")
        self.assertIn("今天的市场", result["markdown"])
        self.assertIn("最近强的方向", result["markdown"])
        self.assertIn("哪些信号值得留意", result["markdown"])
        self.assertIn("15.0%", result["markdown"])
        self.assertIn("下一交易日先观察", result["markdown"])

    def test_rising_market_does_not_claim_retreat(self):
        self.derived['breadth'].update(up=90, down=10, median_pct=2)
        self.derived['phase']['phase'] = '发酵期'
        result = build_narrative(self.derived, self.sectors, {})
        self.assertIn('上涨的股票更多', result['markdown'])
        self.assertNotIn('退潮', result['markdown'])

    def test_mismatched_sectors_are_excluded(self):
        self.sectors['date'] = '2026-11-12'
        result = build_narrative(self.derived, self.sectors, {})
        self.assertNotIn('甲主题', result['markdown'])
        self.assertTrue(any('统计日不同' in x for x in result['missing']))

    def test_missing_numbers_are_not_zero(self):
        self.derived['promotion'] = {}
        self.derived['premium'] = {}
        result = build_narrative(self.derived, self.sectors, {})
        self.assertNotIn('占 0.0%', result['markdown'])
        self.assertNotIn('中位数为 +0.00%', result['markdown'])

    def test_missing_sectors_is_explicit(self):
        result = build_narrative(self.derived, None, {})
        self.assertTrue(any("板块轮动数据缺失" in item for item in result["missing"]))
        self.assertTrue(any("无法回答今天炒什么" in item for item in result["missing"]))

    def test_missing_derived_does_not_invent_story(self):
        result = build_narrative(None, self.sectors, {})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["markdown"], "")


if __name__ == "__main__":
    unittest.main()

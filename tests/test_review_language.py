import json
import unittest
from pathlib import Path

import pandas as pd

import evening_plan
import review_language


class ReviewLanguageTests(unittest.TestCase):
    def test_cached_plan_text_is_rewritten_without_value_judgements(self):
        old = {
            "pools": {
                "pool": {
                    "title": "⚡ 10:00前硬核首板池（#125 十点前涨停 × #030 小盘低价）",
                    "methodology_note": (
                        "依据 #125 与 #030：全天最值得跟踪的首板集中在 09:30~10:00 之间换手封死的标的"
                        "（代表主力早盘做多意愿坚决、全天经受市场抛压检验），叠加流通市值越小、股价越低弹性越佳。"
                    ),
                    "next_day_anchor": "次日 09:25 观察是否高开（+2%~+6%佳），分歧换手放量晋级者锁定。",
                    "pk_groups": [{
                        "rule_hint": "【独苗空间标】当前 3 板唯一身位，代表市场情绪高度锚点（#063）。"
                    }],
                }
            },
            "risks": [
                "🚨 换手极值 73.0%（#096 ≥70%高危区）",
                "ℹ️ 历史 3 次首板均未连板（#032 股性基因）",
            ],
        }
        result = review_language.current_wording(old)
        text = str(result)
        for banned in ("最值得跟踪", "主力", "弹性越佳", "高危区", "股性基因", "锁定"):
            self.assertNotIn(banned, text)
        self.assertIn("10:00前换手首板池", result["pools"]["pool"]["title"])
        self.assertIn("档案内 3 次涨停均止步首板", result["risks"][1])
        rule = result["pools"]["pool"]["pk_groups"][0]["rule_hint"]
        self.assertNotIn("空间标", rule)
        self.assertNotIn("情绪高度锚点", rule)
        self.assertIn("当前 3 板组仅 1 只", rule)

    def test_low_base_first_board_days_remain_in_pool_scorecard(self):
        history = pd.DataFrame(
            [
                dict(date="20260101", code="600001", name="甲", industry="测试",
                     limit_times=1, first_time="09:45:00"),
                dict(date="20260102", code="600001", name="甲", industry="测试",
                     limit_times=2, first_time="09:40:00"),
                dict(date="20260102", code="600002", name="乙", industry="测试",
                     limit_times=1, first_time="09:50:00"),
                dict(date="20260103", code="600002", name="乙", industry="测试",
                     limit_times=2, first_time="09:35:00"),
                dict(date="20260103", code="600003", name="丙", industry="测试",
                     limit_times=1, first_time="09:55:00"),
            ]
        )
        result = evening_plan.compute_pool_scorecard(history)
        self.assertEqual(result["status"], "ok")
        pre = result["strategies"]["pre_10am_first"]
        self.assertGreaterEqual(pre["base"], 2)
        self.assertGreaterEqual(pre["promoted"], 2)

    def test_old_pattern_labels_are_rewritten_to_observable_events(self):
        cached = {
            "pattern_type": "狗啃板(板块仅1只涨停)",
            "methodology_note": (
                "依据 #169：前日长上影试盘测试抛压、今日长下影探底洗盘拉起的"
                "「揉搓线」组合，是主力洗盘结束的经典右侧信号。"
            ),
        }
        current = review_language.current_wording(cached)
        self.assertEqual(
            current["pattern_type"],
            "多次炸板回封(板块仅1只涨停)",
        )
        self.assertNotIn("主力", current["methodology_note"])
        self.assertIn("资金属性", current["methodology_note"])

    def test_full_sentence_replacement_precedes_partial_patterns(self):
        old = (
            "依据 #033/#071：二板是经过首板初筛后的换手定龙节点；若恰逢周五，"
            "#145 统计显示新周期空间龙最高概率出自周五换手二板。"
        )
        current = review_language.current_wording(old)
        self.assertNotIn("定龙", current)
        self.assertNotIn("空间龙", current)
        self.assertNotIn("最高概率", current)
        self.assertNotIn("统计显示#145", current)
        self.assertIn("#145 提出的周五换手二板样本", current)

    def test_old_evaluative_terms_become_observable_counts(self):
        cached = {
            "tags": ["#118 烂而不弱分歧板(炸3次回封)"],
            "watch_targets": "揉搓线/突破回踩：甲、乙",
            "verdict": "今日中位涨幅 -7.31%，大面（≤-5%）2 只",
        }
        current = review_language.current_wording(cached)
        text = str(current)
        for banned in ("烂而不弱", "揉搓线", "大面"):
            self.assertNotIn(banned, text)
        self.assertIn("炸3次", current["tags"][0])
        self.assertIn("跌幅≥5%", current["verdict"])

    def test_loaded_archive_has_no_unverified_value_judgements(self):
        plan = evening_plan.load_evening_plan()
        self.assertIsNotNone(plan)
        text = json.dumps(plan, ensure_ascii=False)
        for banned in (
            "换手二板定龙", "狗啃板", "龙头首阴", "揉搓线", "硬核首板",
            "主力", "烂而不弱", "极易弱转强", "反包潜质", "高盈亏比",
            "机构负责", "游资进攻", "样本不足", "可参考性低", "最高概率",
            "必然", "坚决定投", "逃顶", "大面", "多龙", "中庸", "空间龙",
            "首阴", "核按钮",
        ):
            self.assertNotIn(banned, text)

    def test_historical_digest_display_is_rewritten_to_current_wording(self):
        archived = list(Path("data/digest/history").glob("*.md"))
        self.assertTrue(archived)
        for path in archived:
            text = review_language.current_wording(path.read_text(encoding="utf-8"))
            for banned in ("最高概率", "极易", "主力承接", "高盈亏比"):
                self.assertNotIn(banned, text, f"{path.name}: {banned}")


if __name__ == "__main__":
    unittest.main()

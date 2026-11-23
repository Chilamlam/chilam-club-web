import inspect
import json
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import daily_review_ai as review_ai
import database
import page_evening_plan
import page_review
import review_evidence
from review_template import DEFAULT_TEMPLATE


NEUTRAL_PLAN = (
    "下一交易日核对：1进2 晋级率是否低于 15%、昨日连板股中位涨幅；"
    "分别记录各组封板、换手与收盘结果。"
)


def full_ai_payload(plan_text=NEUTRAL_PLAN):
    answers = {}
    for question in DEFAULT_TEMPLATE:
        qid = question["id"]
        if qid in review_ai.AI_SKIP_IDS:
            continue
        if question["type"] == "choice_from_data":
            choice = "粮食概念"
        elif question.get("options"):
            choice = question["options"][0]
        else:
            choice = "文字"
        answers[qid] = {"choice": choice, "basis": "连板溢价 -1.0%（18 只）"}
    return {"answers": answers, "plan_text": plan_text}


def review_compare_app():
    import page_review
    page_review._render_compare("20261116", {"answers": {"q03_emotion_cycle": "退潮"}})


def visible_text(app):
    values = []
    for collection in (
        app.markdown,
        app.caption,
        app.info,
        app.warning,
        app.success,
        app.error,
    ):
        values.extend(str(element.value) for element in collection)
    values.extend(str(element.label) for element in app.expander)
    return "\n".join(values)


class AiReviewSemanticsTests(unittest.TestCase):
    def test_form_copy_asks_for_observations_instead_of_trade_actions(self):
        text = json.dumps(DEFAULT_TEMPLATE, ensure_ascii=False)
        for phrase in ("低吸", "追高", "买入", "卖出", "操作计划", "赚钱模式"):
            self.assertNotIn(phrase, text)
        self.assertIn("可核对的价格结构样本", text)
        self.assertIn("次日观察与核对计划", text)

    def test_ladder_question_checks_actual_stocks_not_sample_size(self):
        ladder = next(q for q in DEFAULT_TEMPLATE if q["id"] == "q07_ladder")
        self.assertIn("逐只核对", ladder["prompt"])
        self.assertFalse(ladder["ai_checkable"])
        self.assertIn("q07_ladder", review_ai.AI_SKIP_IDS)
        text = json.dumps(DEFAULT_TEMPLATE, ensure_ascii=False)
        for phrase in ("足够样本", "样本<10", "样本不足", "噪音大", "可参考性"):
            self.assertNotIn(phrase, text)

    def test_retired_ladder_rows_do_not_enter_current_scoring_stats(self):
        legacy_rows = [
            {"trade_date": "2026-11-17", "question_id": "q07_ladder",
             "user_hit": True, "ai_hit": True},
            {"trade_date": "2026-11-17", "question_id": "q03_emotion_cycle",
             "user_hit": True, "ai_hit": False},
        ]
        with patch.object(database, "_supabase_request", return_value=legacy_rows):
            stats = database.get_scoring_stats(
                1, active_question_ids={"q03_emotion_cycle"}
            )
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["user_hits"], 1)
        self.assertEqual(stats["ai_hits"], 0)

    def test_prompt_requires_next_day_checklist_and_forbids_unsupported_language(self):
        prompt = review_ai.build_prompt(
            "20261116",
            {"sector_top10": [{"name": "粮食概念", "pct": 5.0}]},
        )
        self.assertIn("只列下一交易日待核对指标", prompt)
        self.assertIn("今日基准、阈值与观测项", prompt)
        self.assertIn("禁止出现概率或确定性判断", prompt)
        self.assertIn("不评价参考意义", prompt)
        self.assertNotIn("明日预期（结构描述）", prompt)

    def test_requested_unsupported_terms_are_classified(self):
        samples = {
            "大概率延续": "概率判断",
            "主力承接": "资金意图",
            "上方阻力": "技术定性",
            "低吸观察": "交易动作",
            "阶段见顶": "定性结论",
            "样本极小，参考意义有限": "样本判断",
            "目标价 12 元": "价格/收益承诺",
        }
        for text, expected in samples.items():
            with self.subTest(text=text):
                self.assertIn(expected, review_ai.unverified_language_hits(text))

    def test_factual_support_word_is_not_misclassified_as_a_price_level(self):
        self.assertEqual(
            review_ai.unverified_language_hits("连板样本与站内数据支撑该记录"),
            [],
        )
        self.assertIn(
            "技术定性",
            review_ai.unverified_language_hits("现价接近上方阻力位"),
        )
        self.assertIn(
            "技术定性",
            review_ai.unverified_language_hits("跌破支撑"),
        )

    def test_neutral_checklist_is_accepted(self):
        cleaned, problems = review_ai.validate_answer(
            full_ai_payload(),
            {"sector_top10": [{"name": "粮食概念", "pct": 5.0}]},
        )
        self.assertTrue(cleaned)
        self.assertEqual(cleaned["plan_text"], NEUTRAL_PLAN)
        self.assertFalse(any("plan_text:" in problem for problem in problems))

    def test_small_group_counts_remain_as_observable_records(self):
        text = (
            "下一交易日核对：2进3 与 3进4 今日分别 2 只、1 只；"
            "次日记录各组封板、换手与收盘结果。"
        )
        self.assertEqual(review_ai.ai_checklist_issues(text), [])

    def test_unsafe_or_non_checklist_plan_rejects_whole_answer(self):
        cases = {
            "明日大概率延续退潮": "概率判断",
            "主力将继续承接": "资金意图",
            "核对晋级率，再决定仓位": "交易动作",
            "核对数量；样本极小，参考意义有限": "样本判断",
            "核对数量；样本不足": "样本判断",
            "核对阈值，目标价为 12 元": "价格/收益承诺",
            "明日情绪延续退潮": "未列出待核对指标/阈值/观测项",
        }
        ctx = {"sector_top10": [{"name": "粮食概念", "pct": 5.0}]}
        for text, expected in cases.items():
            with self.subTest(text=text):
                cleaned, problems = review_ai.validate_answer(full_ai_payload(text), ctx)
                self.assertEqual(cleaned, {})
                self.assertTrue(any("plan_text:" in problem and expected in problem
                                    for problem in problems), problems)

    def test_template_language_in_any_basis_removes_that_answer(self):
        payload = full_ai_payload()
        payload["answers"]["q03_emotion_cycle"]["basis"] = "主力承接后，明日有望继续修复。"
        cleaned, problems = review_ai.validate_answer(
            payload,
            {"sector_top10": [{"name": "粮食概念", "pct": 5.0}]},
        )
        self.assertNotIn("q03_emotion_cycle", cleaned["answers"])
        self.assertTrue(any("basis 含未验证表述" in problem for problem in problems))


class ReviewEvidenceSemanticsTests(unittest.TestCase):
    def test_popular_evidence_is_only_a_height_group(self):
        result = review_evidence._evd_popular({
            "stocks": [
                {"name": "甲", "code": "600001", "industry": "测试", "limit_times": 3},
                {"name": "乙", "code": "600002", "industry": "测试", "limit_times": 2},
            ]
        })
        text = json.dumps(result, ensure_ascii=False)
        self.assertIn("连板高度分组", text)
        self.assertIn("仅按连板高度分组", text)
        for banned in ("人气核心区", "人气股参考", "高板池"):
            self.assertNotIn(banned, text)

    def test_pattern_evidence_uses_neutral_ranking_labels(self):
        ladder = {
            "stocks": [
                {"name": "甲", "code": "600001", "industry": "测试", "limit_times": 3},
                {"name": "乙", "code": "600002", "industry": "测试", "limit_times": 2},
            ]
        }
        rows = {
            review_evidence.SNAPSHOT_PATH: [
                {"date": "20261116", "name": "甲", "amount": "100000", "industry": "测试"},
            ],
            review_evidence.STRONG_PATH: [
                {"更新日期": "20261116", "name": "乙", "RPS_50": "95", "turnover_rate": "12"},
            ],
        }
        with patch.object(
            review_evidence,
            "_load_csv",
            side_effect=lambda path: rows.get(path, []),
        ), patch.object(
            review_evidence,
            "_load_json",
            return_value={"date": "20261116", "watch": {"today_leaders": []}},
        ):
            result = review_evidence._evd_patterns("20261116", ladder)

        text = json.dumps(result, ensure_ascii=False)
        for expected in (
            "RPS50 排名前列",
            "仅相对强度排名",
            "换手率排名前列",
            "仅池内排序",
            "2板及以上样本",
            "站内暂无归档，不生成候选",
        ):
            self.assertIn(expected, text)
        for banned in ("趋势低吸", "人气低吸", "凭盘感", "高板人气池", "连板接力"):
            self.assertNotIn(banned, text)

    def test_compare_view_rejects_old_template_text(self):
        source = inspect.getsource(page_review._render_compare)
        self.assertIn("AI 次日核对清单", source)
        self.assertIn("答案引用的站内记录", source)
        self.assertIn("unverified_language_hits", source)
        self.assertIn("ai_checklist_issues", source)
        self.assertIn("已隐藏", source)
        self.assertNotIn("AI 明日预期", source)
        self.assertNotIn('AI 依据：', source)

    def test_compare_view_renders_safe_checklist(self):
        ai_row = {
            "answers": {
                "q03_emotion_cycle": {
                    "choice": "退潮",
                    "basis": "连板溢价 -1.0%（18 只）",
                }
            },
            "plan_text": NEUTRAL_PLAN,
        }
        with patch.object(page_review.auth, "get_current_user", return_value={"user_id": 1}), \
             patch.object(page_review.database, "get_ai_review", return_value=(ai_row, None)), \
             patch.object(page_review.review_evidence, "build_evidence", return_value={}):
            app = AppTest.from_function(review_compare_app).run(timeout=30)
        self.assertFalse(app.exception)
        text = visible_text(app)
        self.assertIn("AI 次日核对清单", text)
        self.assertIn("答案引用的站内记录", text)
        self.assertIn(NEUTRAL_PLAN, text)
        self.assertNotIn("AI 明日预期", text)
        self.assertNotIn("AI 依据", text)

    def test_compare_view_hides_unsafe_legacy_text(self):
        old_plan = "明日大概率延续退潮，以防守为主并决定仓位节奏"
        old_basis = "主力资金认可，已达到一票否决条件"
        ai_row = {
            "answers": {
                "q03_emotion_cycle": {
                    "choice": "退潮",
                    "basis": old_basis,
                }
            },
            "plan_text": old_plan,
        }
        with patch.object(page_review.auth, "get_current_user", return_value={"user_id": 1}), \
             patch.object(page_review.database, "get_ai_review", return_value=(ai_row, None)), \
             patch.object(page_review.review_evidence, "build_evidence", return_value={}):
            app = AppTest.from_function(review_compare_app).run(timeout=30)
        self.assertFalse(app.exception)
        text = visible_text(app)
        self.assertNotIn(old_plan, text)
        self.assertNotIn(old_basis, text)
        self.assertIn("旧答卷文本未通过当前规则，已隐藏", text)
        self.assertIn("旧 AI 答卷的次日描述未通过当前中立性检查，已隐藏", text)
        self.assertIn("AI：退潮", text)


class EveningPlanHandoffTests(unittest.TestCase):
    def test_observation_memo_has_real_handoffs_and_separates_source_labels(self):
        source = inspect.getsource(page_evening_plan._render_plan_builder)
        self.assertIn("加入观察记录并打开", source)
        self.assertIn("带入复盘答卷", source)
        self.assertIn("下载 Markdown", source)
        self.assertIn("quick_follow", source)
        library = inspect.getsource(page_evening_plan._render_methodology_library)
        self.assertIn("站内整理摘要（作者立场，未经本站验证）", library)
        self.assertIn("作者原文归档（保留原意，不作为站内结论）", library)
        self.assertNotIn("原文资料", library)


if __name__ == "__main__":
    unittest.main()

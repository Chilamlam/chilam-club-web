import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

import decision_workbench as engine
import journal_store
import page_decision_workbench as page


def workbench_app(section="candidates"):
    from page_decision_workbench import render_decision_workbench_page
    render_decision_workbench_page(section)


def plan(mode="模拟观察", **overrides):
    fields = dict(symbol="600519.SH", name="研究样本", style="trend", mode=mode,
                  asof="20261117", data_current=False, thesis="行业订单公告", trigger="站回约定价位",
                  invalid="参考低点失守", exit_rule="三个交易日后复核", cost_risk="考虑费用与跳空",
                  funding_check="待核对", strategy_check="研究假设", capacity="待确认", manual_evidence="")
    fields.update(overrides)
    return engine.new_plan(fields, now="2026-11-17T20:00:00+08:00")


class EvidenceTests(unittest.TestCase):
    def test_mixed_date_snapshot_is_excluded_and_leading_zero_preserved(self):
        frame = pd.DataFrame([dict(ts_code="000001.SZ", name="A", RPS_50=99, 更新日期="20261117"),
                              dict(ts_code="600001.SH", name="B", RPS_50=98, 更新日期="20261116")])
        rows, excluded = engine.candidate_rows("trend", {"strong": frame}, {}, asof="20261117")
        self.assertEqual(rows, [])
        self.assertTrue(any("混合日期" in message for message in excluded))
        rows, _ = engine.candidate_rows("trend", {"strong": frame.iloc[:1]}, {}, asof="20261117")
        self.assertEqual(rows[0]["symbol"], "000001.SZ")

    def test_union_requires_same_date_and_keeps_risks_and_market_identity(self):
        frame = pd.DataFrame([dict(ts_code="000905.SZ", name="A", RPS_50=99, 更新日期="20261117"),
                              dict(ts_code="000905.SH", name="B", RPS_50=98, 更新日期="20261117")])
        evening = dict(date="20261117", status="complete", pools={"pool_rubbing_breakout": {
            "title": "回踩", "items": [dict(code="000905", name="A", risks=["风险待核对"])]}})
        rows, _ = engine.candidate_rows("trend", {"strong": frame}, evening, asof="20261117")
        self.assertEqual(len(rows), 2)
        sz = next(r for r in rows if r["symbol"] == "000905.SZ")
        self.assertEqual(len(sz["reasons"]), 2)
        self.assertEqual(sz["risks"], ["风险待核对"])
        evening["date"] = "20261116"
        rows, _ = engine.candidate_rows("trend", {"strong": frame}, evening, asof="20261117")
        self.assertTrue(all(not row["risks"] for row in rows))

    def test_missing_premium_is_not_zero_and_history_has_no_lookahead(self):
        market = pd.DataFrame([dict(date=f"202610{day:02d}", total_amount_yi=100) for day in range(1, 21)] +
                              [dict(date="20261117", total_amount_yi=200), dict(date="20261118", total_amount_yi=99999)])
        result = engine.market_evidence({"date": "20261117"}, market, {}, asof="20261117")
        facts = {r["label"]: r["value"] for r in result["facts"]}
        self.assertIsNone(facts["昨日连板股今日中位涨跌幅"])
        self.assertEqual(facts["成交额 / 前20个观测日均额"], 2)
        market.loc[3, "total_amount_yi"] = None
        result = engine.market_evidence({}, market, {}, asof="20261117")
        self.assertNotIn("成交额 / 前20个观测日均额", [r["label"] for r in result["facts"]])


class JournalTests(unittest.TestCase):
    def test_tracking_never_equates_missing_pool_with_failed_trade(self):
        record = plan()
        check = engine.tracking_evidence(record, {}, {}, asof="20261117")
        self.assertIn("尚无更新", check["message"])
        check = engine.tracking_evidence(record, {}, {}, asof="20261118")
        self.assertIn("部分来源缺失", check["message"])
        self.assertIsNone(check["current"])

    def test_checks_are_not_overridden_by_positive_ranking(self):
        record = plan()
        self.assertGreaterEqual(len(record["plan"]["checks"]), 4)
        self.assertIn("历史研究", record["plan"]["checks"][0])
        with self.assertRaises(ValueError):
            plan(trigger="")
        with self.assertRaises(ValueError):
            plan(symbol="600519")

    def test_outcomes_preserve_original_and_reject_incomplete_money(self):
        original = plan()
        frozen = copy.deepcopy(original)
        result = engine.with_outcome(original, dict(status="已结束", gross_pnl=100, costs=20,
                                                   closed_on="2026-11-18", notes="等待生效"))
        self.assertEqual(original, frozen)
        self.assertEqual(result["plan"], original["plan"])
        self.assertEqual(result["outcome"]["net_pnl"], 80)
        for changes in ({"costs": -1}, {"costs": None}, {"gross_pnl": float("nan")}, {"closed_on": "20261116"}):
            with self.assertRaises(ValueError):
                engine.with_outcome(original, dict(result["outcome"], **changes))
        reopened = engine.with_outcome(result, dict(result["outcome"], status="已执行"))
        self.assertNotIn("net_pnl", reopened["outcome"])

    def test_real_and_simulated_samples_stay_separate_and_breakeven_counts(self):
        records = []
        for gross, cost in [(110, 10), (-40, 10), (10, 10)]:
            records.append(engine.with_outcome(plan(), dict(status="已结束", gross_pnl=gross,
                costs=cost, closed_on="20261118", discipline="按计划")))
        records.append(engine.with_outcome(plan(mode="实盘记录"), dict(status="已结束", gross_pnl=999999,
            costs=0, closed_on="20261118")))
        records.append(plan())
        result = engine.journal_stats(records, mode="模拟观察")
        self.assertEqual(result["n"], 3)
        self.assertEqual(result["pending"], 1)
        self.assertAlmostEqual(result["win_rate"], 1/3)
        self.assertEqual(result["payoff"], 2)
        self.assertAlmostEqual(result["expectancy"], 50/3)
        self.assertIsNone(engine.journal_stats([records[0]], mode="模拟观察")["payoff"])

    def test_restore_validates_records_and_never_trusts_saved_net_or_private_flags(self):
        original = engine.with_outcome(plan(), dict(status="已结束", gross_pnl=100, costs=30, closed_on="20261118"))
        original.update(_synced=True, _dirty=False)
        exported = engine.export_journal([original])
        self.assertNotIn("_synced", exported)
        payload = json.loads(exported)
        payload["records"][0]["outcome"]["net_pnl"] = 999999
        restored = engine.import_journal(json.dumps(payload))
        self.assertEqual(restored[0]["outcome"]["net_pnl"], 70)
        self.assertNotIn("_synced", restored[0])
        self.assertTrue(restored[0]["_imported"])
        payload["records"].append(payload["records"][0])
        with self.assertRaises(ValueError):
            engine.import_journal(json.dumps(payload))


class CloudJournalTests(unittest.TestCase):
    def test_owner_comes_from_verified_session_and_patch_uses_revision(self):
        record = plan()
        record.update(_synced=True, revision=4)
        with patch.object(journal_store.auth, "get_current_user", return_value={"user_id": 7}), \
             patch.object(journal_store.database, "_supabase_request", return_value=([], None)) as request:
            saved, err = journal_store.save_record(record)
        self.assertIsNone(saved)
        self.assertEqual(err["code"], "CONFLICT")
        self.assertEqual(request.call_args.kwargs["params"]["user_id"], "eq.7")
        self.assertEqual(request.call_args.kwargs["params"]["revision"], "eq.4")
        self.assertNotIn("plan", request.call_args.kwargs["json_data"])

    def test_failed_read_is_not_empty_and_unauthenticated_never_requests(self):
        with patch.object(journal_store.auth, "get_current_user", return_value=None), \
             patch.object(journal_store.database, "_supabase_request") as request:
            self.assertIsNotNone(journal_store.save_record(plan())[1])
            self.assertIsNotNone(journal_store.load_records()[1])
            request.assert_not_called()
        with patch.object(journal_store.auth, "get_current_user", return_value={"user_id": 7}), \
             patch.object(journal_store.database, "_supabase_request", return_value=(None, {"message": "offline"})):
            records, err = journal_store.load_records()
            self.assertIsNone(records)
            self.assertEqual(err["message"], "offline")


class WorkbenchRenderingTests(unittest.TestCase):
    def test_candidate_filter_clear_uses_a_before_widget_callback(self):
        frames = {
            "strong": pd.DataFrame([dict(
                ts_code="600519.SH", name="研究样本", industry="白酒",
                RPS_50=98, 更新日期="20261117",
            )]),
            "breakout": pd.DataFrame(),
            "etf": pd.DataFrame(),
        }
        context = (frames, {}, "20261117", dict(
            expected_date="20261117", status="current", checked_at="2026-11-17 20:00", message="",
        ), dict(facts=[], warnings=[], unavailable=""))
        with patch.object(journal_store.auth, "is_logged_in", return_value=False), \
             patch.object(page, "_context", return_value=context), \
             patch.object(page.insights, "load_insights", return_value={
                 "plans": {}, "calendar": [], "prices": pd.DataFrame(),
             }):
            app = AppTest.from_function(workbench_app, args=("candidates",)).run(timeout=30)
            query = next(w for w in app.text_input if w.label == "查找股票或行业")
            query.input("不存在的标的").run(timeout=30)
            clear = next(b for b in app.button if b.label == "清除筛选并恢复全部候选")
            clear.click().run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual(app.session_state["decision_journal_guest_trend_query"], "")
            self.assertEqual(app.session_state["decision_journal_guest_trend_industries"], [])

    def test_all_styles_render_and_plan_can_be_closed_without_changing_premise(self):
        context = ({}, {}, "20261117", dict(expected_date="20261120", status="delayed",
            checked_at="2026-11-20 20:00", message="本地历史数据"),
            dict(facts=[], warnings=[], unavailable="未接入负债证据"))
        with patch.object(journal_store.auth, "is_logged_in", return_value=False), \
             patch.object(page, "_context", return_value=context):
            app = AppTest.from_function(workbench_app, args=("tracking",)).run(timeout=30)
            self.assertFalse(app.exception)
            def fill(label, value, kind="text_input"):
                next(w for w in getattr(app, kind) if w.label == label).input(value)
            fill("证券代码（含交易所）", "600519.SH")
            fill("名称", "测试")
            next(w for w in app.date_input if w.label == "这条依据的日期").set_value("2026-11-17")
            fill("这次为什么关注", "可验证条件")
            next(b for b in app.button if b.label == "建立研究档案").click().run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            records = app.session_state["decision_journal_guest"]
            self.assertEqual(len(records), 1)
            ident = next(iter(records))
            original = copy.deepcopy(records[ident]["plan"])
            next(w for w in app.selectbox if w.label == "跟踪状态").select("已结束")
            next(w for w in app.number_input if w.label.startswith("毛盈亏")).set_value(100.0)
            next(w for w in app.number_input if w.label.startswith("总成本")).set_value(10.0)
            next(w for w in app.date_input if w.label == "结束日期").set_value("2026-11-20")
            next(b for b in app.button if b.label == "保存跟踪结果").click().run(timeout=30)
            app.run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            record = app.session_state["decision_journal_guest"][ident]
            self.assertEqual(record["outcome"]["net_pnl"], 90)
            self.assertEqual(record["plan"], original)


if __name__ == "__main__":
    unittest.main()

import ast
import datetime as dt
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from streamlit.testing.v1 import AppTest

import auth
import database
import fund_review as funds
import investor_journey as journey
import page_review as review
import page_watchlist as watch
import review_evidence as evidence
import site_data
from update_schedule import CST


def journey_app():
    import pandas as pd
    import streamlit as st
    import investor_journey as journey
    from navigation import NAV_GROUPS
    journey.apply_handoff(st.session_state)
    group = st.selectbox("功能分组", list(NAV_GROUPS), key="nav_group")
    page = st.radio("导航菜单", NAV_GROUPS[group][1], key=f"nav_page_{group}")
    if page == journey.QUOTE_PAGE:
        journey.render_symbol_workspace(st.session_state.active_symbol, "测试标的", key="test")
    else:
        journey.candidate_actions(pd.DataFrame([
            {"ts_code": "000905.SZ", "name": "深市股票"},
            {"ts_code": "000905.SH", "name": "沪市指数"},
        ]), key="test_pick")


def review_form_app():
    import page_review
    page_review._render_form("20261116", {"plan_text": "已有答卷草稿", "answers": {}})


class FundComparisonTests(unittest.TestCase):
    def test_same_day_lagged_future_and_other_code_are_distinct(self):
        rows = [
            {"ts_code": "510300.SH", "nav_date": "20261116", "unit_nav": 2.0},
            {"ts_code": "510300.SH", "nav_date": "20261118", "unit_nav": 10.0},
            {"ts_code": "159919.SZ", "nav_date": "20261117", "unit_nav": .5},
        ]
        same = funds.nav_comparison(2.2, "2026-11-16", rows, code="510300.SH")
        self.assertEqual(same["premium_rate"], 10.0)
        self.assertIsNone(same["reference_gap"])
        lagged = funds.nav_comparison(2.2, "20261117", rows, code="510300.SH")
        self.assertIsNone(lagged["premium_rate"])
        self.assertEqual(lagged["reference_gap"], 10.0)
        self.assertEqual(lagged["nav_date"], "20261116")

    def test_conflicts_and_invalid_values_never_become_zero_premium(self):
        rows = [{"ts_code": "x", "nav_date": "20261116", "unit_nav": n} for n in (1, 2)]
        result = funds.nav_comparison(2, "20261116", rows, code="x")
        self.assertEqual(result["nav_status"], "同日净值冲突")
        self.assertIsNone(result["premium_rate"])
        for invalid in (None, True, 0, -1, float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                result = funds.nav_comparison(2, "20261116", [
                    {"ts_code": "x", "nav_date": "20261116", "unit_nav": invalid}], code="x")
                self.assertIsNone(result["premium_rate"])
                self.assertIsNone(result["reference_gap"])

    def test_display_recomputes_instead_of_trusting_old_zero_or_fake_status(self):
        rows = funds.display_funds([
            {"code": "x", "price": 2, "premium_rate": 0, "est_premium": 0, "nav_status": "同日净值"},
            {"code": "x", "price": 2.2, "quote_date": "20261116", "nav_date": "20261116",
             "nav": 2, "premium_rate": 999},
            {"code": "x", "price": 2, "quote_date": "20261116", "nav_date": "20261117",
             "nav": 2, "premium_rate": 0},
        ])
        self.assertIsNone(rows[0]["premium_rate"])
        self.assertNotEqual(rows[0]["nav_status"], "同日净值")
        self.assertNotIn("est_premium", rows[0])
        self.assertEqual(rows[1]["premium_rate"], 10)
        self.assertIsNone(rows[2]["premium_rate"])
        self.assertIn("晚于", rows[2]["nav_status"])

    def test_producer_queries_one_code_and_retains_each_nav_date(self):
        # Execute the actual producer function with local inputs and no token/network.
        source = Path(__file__).resolve().parents[1] / "daily_speculation.py"
        node = next(n for n in ast.parse(source.read_text(encoding="utf-8")).body
                    if isinstance(n, ast.FunctionDef) and n.name == "get_fund_arbitrage")
        api = Mock()
        api.fund_nav.side_effect = [
            pd.DataFrame([{"ts_code": "510300.SH", "nav_date": "20261116", "unit_nav": 2}]),
            pd.DataFrame([{"ts_code": "159919.SZ", "nav_date": "20261113", "unit_nav": 1}]),
        ]
        namespace = {"pro": api, "pd": pd, "datetime": dt, "nav_comparison": funds.nav_comparison,
                     "requests": Mock(get=Mock(return_value=Mock(text=""))),
                     "get_latest_trade_date": lambda _: ("20261116", pd.DataFrame([
                         {"ts_code": "510300.SH", "close": 2.2, "pct_chg": 2, "amount": 100000},
                         {"ts_code": "159919.SZ", "close": 1.1, "pct_chg": 1, "amount": 20000},
                     ]))}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        rows = namespace["get_fund_arbitrage"]()
        self.assertEqual([c.kwargs["ts_code"] for c in api.fund_nav.call_args_list], ["510300.SH", "159919.SZ"])
        self.assertTrue(all(c.kwargs["end_date"] == "20261116" for c in api.fund_nav.call_args_list))
        self.assertEqual(rows[0]["premium_rate"], 10)
        self.assertIsNone(rows[1]["premium_rate"])
        self.assertEqual(rows[1]["reference_gap"], 10)


class JourneyTests(unittest.TestCase):
    def test_market_identity_and_one_time_handoff(self):
        self.assertEqual(journey.explicit_symbol("000905.SZ"), "sz000905")
        self.assertEqual(journey.explicit_symbol("000905.SH"), "sh000905")
        self.assertEqual(journey.explicit_symbol("000905"), "000905")
        self.assertEqual(journey.equity_symbol("000905"), "sz000905")
        self.assertEqual(journey.equity_symbol("920002"), "bj920002")
        state = {"journey_target": {"page": journey.QUOTE_PAGE, "symbol": "000905.SZ"}}
        journey.apply_handoff(state)
        self.assertEqual(state["nav_group"], "找标的")
        self.assertEqual(state["nav_page_找标的"], journey.QUOTE_PAGE)
        self.assertEqual(state["active_symbol"], "sz000905")
        self.assertEqual(state["period_A_SHARE"], "日K")
        self.assertNotIn("journey_target", state)
        before = dict(state)
        journey.apply_handoff(state)
        self.assertEqual(state, before)

    def test_transfer_appends_once_and_never_crosses_accounts(self):
        state = {"observation_review_text": {"owner": "account1", "text": "新观察"}}
        text, changed = journey.take_review_plan(state, "account1", "已有草稿")
        self.assertTrue(changed)
        self.assertEqual(text, "已有草稿\n\n新观察")
        self.assertEqual(journey.take_review_plan(state, "account1", text), (text, False))
        state = {"observation_review_text": {"owner": "account1", "text": "别人的计划"}}
        self.assertEqual(journey.take_review_plan(state, "account2", "自己的草稿"), ("自己的草稿", False))

    def test_real_widgets_share_journal_and_preserve_failed_cloud_write(self):
        import journal_store
        from tests.test_decision_workbench import plan
        record = plan(symbol="000905.SZ")
        record.update(_synced=True, revision=1)
        with patch.object(auth, "get_current_user", return_value={"user_id": 11}), \
             patch.object(journal_store, "load_records", return_value=([record], None)), \
             patch.object(journal_store, "save_record", return_value=(None, {"code":"offline"})) as write:
            app = AppTest.from_function(journey_app).run(timeout=30)
            app.selectbox(key="test_pick_pick").select("sz000905").run(timeout=30)
            app.button(key="test_pick_chart").click().run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="nav_group").value, "找标的")
            self.assertEqual(app.session_state["active_symbol"], "sz000905")
            write.assert_not_called()
            next(w for w in app.text_area if w.label == "下一步要等什么").input("重新站回前高")
            next(b for b in app.button if b.label == "保存研究与条件").click().run(timeout=30)
            write.assert_called_once()
            saved = app.session_state["decision_journal_11"][record["id"]]
            self.assertEqual(saved["outcome"]["research"]["trigger"], "重新站回前高")
            self.assertTrue(saved["_dirty"])
            self.assertEqual(saved["plan"], record["plan"])
            self.assertTrue(any("账号暂时无法" in e.value for e in app.error))
            self.assertFalse(app.exception)

    def test_review_plan_survives_failed_save_without_overwriting_draft(self):
        with patch.object(auth, "is_logged_in", return_value=True), \
             patch.object(auth, "get_user_id", return_value=11), \
             patch.object(auth, "get_current_user", return_value={"user_id": 11}), \
             patch.object(review, "_sector_options", return_value=[]), \
             patch.object(evidence, "build_evidence", return_value={}), \
             patch.object(database, "upsert_review_answer", return_value=(None, {"message": "offline"})) as save:
            app = AppTest.from_function(review_form_app)
            app.session_state["observation_review_text"] = {"owner": "observation_plans_11", "text": "新增观察条件"}
            app.run(timeout=30)
            plan_key = "rv_observation_plans_11_20261116_q15_tomorrow_plan"
            self.assertEqual(app.text_area(key=plan_key).value, "已有答卷草稿\n\n新增观察条件")
            next(b for b in app.button if b.label == "💾 保存草稿").click().run(timeout=30)
            self.assertEqual(save.call_args.args[3], "已有答卷草稿\n\n新增观察条件")
            self.assertTrue(any("offline" in e.value for e in app.error))
            self.assertEqual(app.text_area(key=plan_key).value.count("新增观察条件"), 1)
            self.assertFalse(app.exception)


def quote(code, pct=1, day="20261116"):
    return dict(name=code, price=10, last_close=9, pct_chg=pct, high=11, low=9,
                turnover=1, amplitude=2, float_mv_yi=10, update_time=day + "150000",
                tx_code=code, kind="个股", alive=True, amount=100)


class WatchEvidenceTests(unittest.TestCase):
    def test_explicit_markets_do_not_collide_with_each_other_or_benchmark(self):
        quotes = {code: quote(code, value) for code, value in
                  (("sz000905", 3), ("sh000905", -2), ("sh000300", 1), ("sz000300", 7))}
        watch.fetch_quotes.clear()
        with patch.object(watch.sr, "fetch_quotes", return_value=quotes):
            got = watch.fetch_quotes(("000905.SZ", "000905.SH", "000300.SZ"))
        watch.fetch_quotes.clear()
        self.assertEqual(got["sz000905"]["pct"], 3)
        self.assertEqual(got["sh000905"]["pct"], -2)
        self.assertEqual(got["sz000300"]["pct"], 7)
        self.assertEqual(got["000300"]["tx_code"], "sh000300")

    def test_joins_preserve_identity_and_gate_old_or_unknown_source_dates(self):
        q = {k: {"name": k, "price": 10, "pct": v, "tx_code": k, "time": day + "150000"}
             for k, v, day in (("sz000905", 3, "20261116"), ("sh000905", -2, "20261116"),
                               ("sh000300", 1, "20261116"), ("sz000001", 8, "20261113"))}
        frames = {"data/strong_stocks.csv": pd.DataFrame([
            {"ts_code": "000905.SZ", "RPS_50": 99, "更新日期": "20261113"}]),
            "data/breakout_stocks.csv": pd.DataFrame([
                {"ts_code": "000905.SZ", "level": "60日突破", "update_date": "20261116"}]),
            "data/strong_etfs.csv": pd.DataFrame([{"ts_code": "000905.SZ"}])}
        payloads = {"data/limit_ladder.json": {"date": "20261116", "stocks": [{"code": "000905", "limit_times": 2}]}}
        with patch.object(watch, "fetch_quotes", return_value=q), \
             patch.object(watch, "_load_csv", side_effect=lambda p: frames.get(p, pd.DataFrame())), \
             patch.object(watch, "_load_json", side_effect=lambda p: payloads.get(p, {})), \
             patch.object(watch, "beijing_now", return_value=dt.datetime(2026, 11, 17, tzinfo=CST)):
            rows, market, src = watch._build_rows(["000905.SZ", "000905.SH", "000001.SZ", "600001.SH"])
        self.assertEqual(market, 1)
        self.assertIn("60日突破", str(rows[0]["_signals"]))
        self.assertIn("2连板", str(rows[0]["_signals"]))
        self.assertNotIn("RPS榜", str(rows[0]["_signals"]))
        self.assertNotIn("ETF榜", str(rows[0]["_signals"]))
        self.assertEqual(rows[1]["_signals"], [])
        self.assertFalse(rows[2]["_comparison_ok"])
        self.assertIsNone(rows[3]["涨跌幅"])
        self.assertFalse(rows[3]["_has_quote"])
        self.assertTrue(any("RPS" in text for text in src["未采用来源"]))

    def test_review_date_is_published_open_day_not_calendar_yesterday(self):
        calendar = {"start_date": "20261101", "end_date": "20261130", "open_dates": ["20261113", "20261116"]}
        paths = {"data/trading_calendar.json": calendar, "data/sentiment/derived.json": {"date": "20261113"},
                 "data/limit_ladder.json": {"date": "20261116"}}
        with patch.object(review, "load_json", side_effect=lambda p: paths.get(p, {})):
            self.assertEqual(review._current_trade_date(dt.datetime(2026, 11, 16, 10, tzinfo=CST)), "20261113")
            self.assertEqual(review._current_trade_date(dt.datetime(2026, 11, 16, 19, tzinfo=CST)), "20261116")
            paths["data/limit_ladder.json"] = {"date": "20261115"}  # Sunday is not a valid target.
            self.assertEqual(review._current_trade_date(dt.datetime(2026, 11, 16, 19, tzinfo=CST)), "20261113")

    def test_daily_sector_order_is_not_ten_day_order_and_requires_coverage(self):
        rows = [{"date": "20261116", "code": "BK1", "name": "影视", "pct_chg": 1, "pct_10d": 30},
                {"date": "20261116", "code": "BK2", "name": "机器人", "pct_chg": 4, "pct_10d": 2},
                {"date": "20261117", "code": "BK3", "name": "未来", "pct_chg": 20}]
        with patch.object(evidence, "_load_csv", return_value=rows), \
             patch.object(evidence, "_load_json", return_value={"date": "20261116", "universe_count": 2}):
            self.assertEqual([r["name"] for r in evidence.daily_sector_rows("20261116")], ["机器人", "影视"])
            self.assertEqual(evidence.daily_sector_rows("20261113"), [])
            rows.pop(1)
            self.assertEqual(evidence.daily_sector_rows("20261116"), [])

    def test_index_and_snapshot_reference_never_use_future_or_undated_rows(self):
        rows = [{"date": d, "zz1000": 100, "zz500": 100, "spread": 0} for d in ("2026-11-13", "2026-11-16", "2026-11-17")]
        with patch.object(evidence, "_load_csv", return_value=rows):
            self.assertEqual([r[0] for r in evidence._index_tail("20261116")], ["2026-11-13", "2026-11-16"])
            self.assertEqual(evidence._index_tail("20261112"), [])
        snapshots = [{"date": "20261116", "name": "同日", "amount": "100000", "industry": "测试"},
                     {"date": "20261117", "name": "未来", "amount": "99999999"},
                     {"name": "无日期", "amount": "99999999"}]
        with patch.object(evidence, "_load_csv", side_effect=lambda p: snapshots if p == evidence.SNAPSHOT_PATH else []):
            text = str(evidence._evd_recognition("20261116", {}))
        self.assertIn("1.0亿", text)
        self.assertNotIn("未来", text)
        self.assertNotIn("无日期", text)


class ArtifactCacheTests(unittest.TestCase):
    def test_replacements_invalidate_cache_and_preserve_leading_zeros(self):
        with TemporaryDirectory() as tmp, patch.object(site_data, "ROOT", Path(tmp)):
            path = Path(tmp) / "sample.csv"
            self.assertTrue(site_data.load_csv("sample.csv").empty)
            path.write_text("code,value\n000905,1\n", encoding="utf-8")
            self.assertEqual(site_data.load_csv("sample.csv").iloc[0]["code"], "000905")
            stamp = path.stat().st_mtime_ns
            path.write_text("code,value\n000905,2\n", encoding="utf-8")
            os.utime(path, ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))
            self.assertEqual(site_data.load_csv("sample.csv").iloc[0]["value"], 2)
            path = Path(tmp) / "sample.json"
            path.write_text(json.dumps({"value": 1}), encoding="utf-8")
            self.assertEqual(site_data.load_json("sample.json"), {"value": 1})
            stamp = path.stat().st_mtime_ns
            path.write_text(json.dumps({"value": 2}), encoding="utf-8")
            os.utime(path, ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))
            self.assertEqual(site_data.load_json("sample.json"), {"value": 2})
        site_data._csv.clear()
        site_data._json.clear()


if __name__ == "__main__":
    unittest.main()

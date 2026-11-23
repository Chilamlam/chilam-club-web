import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

import market_insights as mi
import workbench_insights_ui as ui
from daily_funding import normalize_margin
from daily_ladder_themes import archive_attribution
from daily_scorecard import fund_adj_snapshot

DAYS = ["20261110", "20261111", "20261112", "20261113", "20261116", "20261117"]
CAL = dict(start_date="20260101", end_date="20261231", open_dates=DAYS)


def sector_history():
    rows = []
    for day in DAYS:
        for i in range(12):
            rows.append(dict(date=day, code=f"BK{i:04}", name=f"题材{i}", pct_chg=2-i/5,
                             pct_5d=10-i, pct_10d=20-i, pct_20d=25-i, up_count=60, down_count=40))
    return pd.DataFrame(rows)


def attributed(day, codes):
    ladder = dict(date=day, stocks=[dict(code=c, name=c, limit_times=b) for c,b in codes])
    payload = dict(date=day, sources={"ths_public": {"status": "ok", "records": {
        c: dict(code=c, date=day, theme="光刻机+半导体", reason="") for c,b in codes}}})
    return dict(date=day, ladder=ladder, attribution=payload)


class ThemeTests(unittest.TestCase):
    def test_price_style_groups_cannot_become_market_themes(self):
        history = sector_history()
        extra = pd.DataFrame([dict(date=d, code=f"X{i}", name=name, pct_chg=90, pct_5d=90,
                                  pct_10d=90, pct_20d=90, up_count=99, down_count=1)
                              for d in DAYS for i,name in enumerate(("历史新高", "反转股", "2026三季报预增"))])
        result = mi.theme_continuity(pd.concat([history, extra]), CAL, DAYS[-1])
        self.assertTrue(all(not r["code"].startswith("X") for r in result["rows"]))
    def test_persistence_and_break_in_archive_do_not_fake_streak(self):
        history = sector_history()
        result = mi.theme_continuity(history, CAL, DAYS[-1])
        first = next(r for r in result["rows"] if r["code"] == "BK0000")
        self.assertEqual(first["streak"], 5)
        self.assertEqual(first["appearances"], 5)
        self.assertEqual(first["state"], "近5次快照入榜5次")
        broken = history[history["date"] != DAYS[-2]]
        result = mi.theme_continuity(broken, CAL, DAYS[-1])
        first = next(r for r in result["rows"] if r["code"] == "BK0000")
        self.assertEqual(first["streak"], 1)
        self.assertEqual(first["observed"], 4)
        self.assertEqual(first["state"], "近4次快照入榜4次")
        self.assertIsNone(result["followthrough"])

    def test_prior_leaders_are_selected_before_todays_rank_and_future_ignored(self):
        history = sector_history()
        mask = (history["date"] == DAYS[-1]) & (history["code"] == "BK0000")
        history.loc[mask, ["pct_10d", "pct_chg", "up_count", "down_count"]] = [-99, -8, 10, 90]
        future = history[history["date"] == DAYS[-1]].assign(date="20261118", pct_chg=99)
        result = mi.theme_continuity(pd.concat([history, future]), CAL, DAYS[-1])
        first = next(r for r in result["rows"] if r["code"] == "BK0000")
        self.assertEqual(first["appearances"], 4)
        self.assertLess(first["breadth"], .2)
        self.assertEqual(result["followthrough"]["retained"], 9)
        self.assertLess(result["followthrough"]["median"], 3)

    def test_partial_day_and_missing_internal_breadth_never_look_strong(self):
        history = sector_history()
        history.loc[history["code"] == "BK0000", "up_count"] = float("nan")
        result = mi.theme_continuity(history, CAL, DAYS[-1])
        first = next(r for r in result["rows"] if r["code"] == "BK0000")
        self.assertNotEqual(first["state"], "持续强势，跟随面较广")
        partial = history[~((history["date"] == DAYS[-1]) & (history["code"] > "BK0005"))]
        self.assertEqual(mi.theme_continuity(partial, CAL, DAYS[-1])["rows"], [])

    def test_leaders_use_prior_height_and_attribution_never_industry(self):
        old = attributed(DAYS[-2], [("000001",3),("000002",1)])
        new = attributed(DAYS[-1], [("000001",1),("000002",4)])
        prices = pd.DataFrame([dict(date=d, ts_code=c, adj_close=p) for d,c,p in [
            (DAYS[-2],"000001.SZ",10),(DAYS[-2],"000002.SZ",10),
            (DAYS[-1],"000001.SZ",9),(DAYS[-1],"000002.SZ",11)]])
        result = mi.theme_leader_followers("光刻机", {DAYS[-2]:old}, new["ladder"], new["attribution"], prices, CAL, DAYS[-1])
        self.assertAlmostEqual(result["leaders"]["median"], -10)
        self.assertAlmostEqual(result["followers"]["median"], 10)
        self.assertEqual(len(result["current"]), 2)
        self.assertEqual(mi.attributed_members("光刻", new["ladder"], new["attribution"], DAYS[-1]), [])
        self.assertIsNone(mi.theme_leader_followers("光刻机", {}, new["ladder"], new["attribution"], prices, CAL, DAYS[-1])["leaders"])

    def test_archiving_failed_retry_retains_same_day_evidence(self):
        old = attributed(DAYS[-1], [("000001",3)])
        with tempfile.TemporaryDirectory() as folder:
            path = archive_attribution(old["ladder"], old["attribution"], folder)
            archive_attribution(old["ladder"], dict(date=DAYS[-1], sources={}), folder)
            payload = json.loads(path.read_text(encoding="utf8"))
            self.assertEqual(len(mi.attributed_members("光刻机", payload["ladder"], payload["attribution"], DAYS[-1])), 1)
            with self.assertRaises(ValueError):
                archive_attribution(dict(old["ladder"], date=DAYS[-2]), old["attribution"], folder)


class FundingAndCohortTests(unittest.TestCase):
    def test_etf_prices_join_same_date_factor_without_using_raw_price_as_fallback(self):
        from unittest.mock import Mock
        client = Mock()
        client.fund_daily.return_value = pd.DataFrame([
            dict(ts_code="159915.SZ", trade_date=DAYS[-1], close=2),
            dict(ts_code="510300.SH", trade_date=DAYS[-1], close=4)])
        client.fund_adj.return_value = pd.DataFrame([
            dict(ts_code="159915.SZ", trade_date=DAYS[-1], adj_factor=1.5),
            dict(ts_code="510300.SH", trade_date=DAYS[-2], adj_factor=2)])
        with patch("daily_scorecard.pro", client):
            result = fund_adj_snapshot(DAYS[-1], {"159915.SZ", "510300.SH"})
        self.assertEqual(result["ts_code"].tolist(), ["159915.SZ"])
        self.assertEqual(result.iloc[0]["adj_close"], 3)
    def test_margin_requires_both_markets_and_units_and_rejects_conflicting_duplicates(self):
        raw = pd.DataFrame([dict(trade_date=DAYS[-1], exchange_id="SSE", rzye=1e8),
                            dict(trade_date=DAYS[-1], exchange_id="SZSE", rzye=2e8),
                            dict(trade_date=DAYS[-1], exchange_id="BSE", rzye=9e8)])
        self.assertEqual(normalize_margin(raw, DAYS[-1]).iloc[0]["balance_yi"], 3)
        with self.assertRaises(ValueError):
            normalize_margin(raw.iloc[:1], DAYS[-1])
        with self.assertRaises(ValueError):
            normalize_margin(pd.concat([raw, raw.iloc[:1].assign(rzye=8e8)]), DAYS[-1])

    def test_margin_lag_is_in_trading_sessions_and_fixture_source_rejected(self):
        margin = pd.DataFrame([dict(date=d, balance_yi=100+i, source="tushare.margin:SSE+SZSE") for i,d in enumerate(DAYS)])
        result = mi.funding_readout(pd.DataFrame(), margin, CAL, DAYS[-1])
        self.assertAlmostEqual(result["margin"]["change5"], 5)
        result = mi.funding_readout(pd.DataFrame(), margin.iloc[:-1], CAL, DAYS[-1])
        self.assertTrue(result["margin"]["usable"])
        result = mi.funding_readout(pd.DataFrame(), margin.iloc[:-2], CAL, DAYS[-1])
        self.assertFalse(result["margin"]["usable"])
        result = mi.funding_readout(pd.DataFrame(), margin.assign(source="fixture"), CAL, DAYS[-1])
        self.assertIsNone(result["margin"])

    def test_cohorts_include_losers_and_missing_prices_and_exclude_todays_members(self):
        picks = pd.DataFrame([dict(date=DAYS[-2], strategy="rps", ts_code="000001.SZ"),
                             dict(date=DAYS[-2], strategy="rps", ts_code="000002.SZ"),
                             dict(date=DAYS[-1], strategy="rps", ts_code="000003.SZ")])
        prices = pd.DataFrame([dict(date=d, ts_code=c, adj_close=p) for d,c,p in [
            (DAYS[-2],"000001.SZ",10),(DAYS[-1],"000001.SZ",9),
            (DAYS[-2],"000003.SZ",10),(DAYS[-1],"000003.SZ",20)]])
        row = mi.style_outcomes(picks, {}, prices, CAL, DAYS[-1])["trend"][-1]
        self.assertEqual(row["base"], 2)
        self.assertEqual(row["missing"], 1)
        self.assertAlmostEqual(row["median"], -10)
        self.assertFalse(row["usable"])

    def test_unknown_breadth_never_produces_favorable_style(self):
        outcome = dict(date=DAYS[-1], n=10, base=10, missing=0, median=2,
                       up_ratio=.6, missing_sources=[])
        value = mi.style_assessment("trend", {}, pd.DataFrame(), {"trend":[outcome]}, {}, CAL, DAYS[-1])
        self.assertEqual(value["label"], "已归档名单的后续表现")
        self.assertIn("已核对价格 10 只", value["conclusion"])
        self.assertIn("上涨占比", value["conclusion"])


class FollowTests(unittest.TestCase):
    def test_one_click_capture_is_idempotent_and_change_comparison_preserves_original(self):
        records = {}
        candidate = dict(symbol="000001.SZ", name="样本", date=DAYS[-2], reasons=["RPS"],
                         sources=["data/strong_stocks.csv"], risks=[])
        ident, added = ui.quick_follow(candidate, "trend", {}, False, records)
        self.assertTrue(added)
        self.assertEqual(ui.quick_follow(candidate, "trend", {}, False, records), (ident, False))
        current = dict(candidate, sources=["data/breakout_stocks.csv"], risks=["新风险"])
        result = mi.focus_changes(records[ident], current, pd.DataFrame(), DAYS[-1])
        self.assertEqual(result["new_risks"], ["新风险"])
        self.assertEqual(result["lost_sources"], ["data/strong_stocks.csv"])
        self.assertEqual(records[ident]["plan"]["candidate_evidence"]["risks"], [])


def panel_app():
    import streamlit as st
    from workbench_insights_ui import render_outcomes, render_funding, render_themes
    render_outcomes(st.session_state["analysis"])
    render_funding(st.session_state["analysis"])
    render_themes(st.session_state["analysis"], "20261117")


class RenderingTests(unittest.TestCase):
    def test_one_click_follow_appears_in_tracking_without_a_form(self):
        import page_decision_workbench as page
        import auth
        with patch.object(ui, "load_csv", return_value=pd.DataFrame()), patch.object(ui, "load_json", return_value={}):
            analysis = ui.load_insights(DAYS[-1])
        candidate = dict(symbol="000001.SZ", name="样本", date=DAYS[-1], reasons=["RPS50 95"],
                         sources=["data/strong_stocks.csv"], risks=[], industry="银行", rps=95)
        context = ({}, {}, DAYS[-1], dict(expected_date=DAYS[-1], status="current", checked_at="2026-11-17 20:00", message="已更新"), {})
        app = AppTest.from_function(workbench_app)
        with patch.object(page, "_context", return_value=context), patch.object(ui, "load_insights", return_value=analysis), \
             patch.object(page.engine, "candidate_rows", return_value=([candidate], [])), \
             patch.object(auth, "is_logged_in", return_value=False):
            app.run(timeout=30)
            selection = next(w for w in app.selectbox if w.label == "选择要继续研究的标的")
            self.assertIsNone(selection.value)
            selection.select("000001.SZ").run(timeout=30)
            next(b for b in app.button if b.label == "加入观察 · 自动保留依据").click().run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            records = app.session_state["decision_journal_guest"]
            self.assertEqual(len(records), 1)
            self.assertTrue(next(iter(records.values()))["plan"]["quick_follow"])
            self.assertTrue(any("已加入观察" in item.value for item in app.success))

    def test_result_panels_render_with_missing_data_without_fabricating_metrics(self):
        with patch.object(ui, "load_csv", return_value=pd.DataFrame()), patch.object(ui, "load_json", return_value={}):
            analysis = ui.load_insights(DAYS[-1])
        app = AppTest.from_function(panel_app)
        app.session_state["analysis"] = analysis
        app.run(timeout=30)
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(app.metric[0].value, "缺数据")


def workbench_app():
    from page_decision_workbench import render_decision_workbench_page
    render_decision_workbench_page()


if __name__ == "__main__":
    unittest.main()

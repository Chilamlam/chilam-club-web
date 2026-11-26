import copy
import datetime as dt
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

import pe_band as model
import pe_band_data as data


START, END = dt.date(2026, 11, 20), dt.date(2026, 11, 25)


def fixture():
    prices = [dict(date=day, close=price) for day, price in [("2026-11-20", 100), ("2026-11-23", 120),
                                                          ("2026-11-24", 110), ("2026-11-25", 90)]]
    pe = [dict(date=day, pe_ttm=value) for day, value in [("2026-11-20", 20), ("2026-11-21", 5000),
                                                        ("2026-11-23", -5), ("2026-11-25", 10)]]
    return model.align_history(prices, pe, start=START, end=END)


def snapshot():
    frame, coverage = fixture()
    return dict(frame=frame, coverage=coverage, market="HK_SHARE", code="00700", currency="HKD",
                source="test fixture", links={}, retrieved_at="2026-11-26T04:00:00+00:00",
                requested_start=START.isoformat(), cutoff=END.isoformat(), price_basis="未复权收盘价")


class CalculationTests(unittest.TestCase):
    def test_only_same_date_positive_pe_never_fill_or_add_weekends(self):
        frame, coverage = fixture()
        self.assertEqual(coverage["positive_days"], 2)
        self.assertEqual(coverage["nonpositive_days"], 1)
        self.assertEqual(coverage["missing_days"], 1)
        self.assertEqual(coverage["unmatched_valuation_days"], 1)
        self.assertEqual(len(frame), 4)
        self.assertEqual(frame.implied_eps.iloc[0], 5)
        self.assertTrue(frame.implied_eps.iloc[1:3].isna().all())
        bands = model.band_prices(frame, model.band_multiples(frame, "10, 20"))
        self.assertEqual(bands["10 倍"].iloc[0], 50)
        self.assertEqual(bands["20 倍"].iloc[-1], 180)
        self.assertTrue(bands["10 倍"].iloc[1:3].isna().all())

    def test_fixed_quantiles_percentile_and_profit_sensitivity(self):
        frame, _ = fixture()
        multiples = model.band_multiples(frame)
        self.assertEqual([v for _, v in multiples], [11, 12.5, 15, 17.5, 19])
        latest = model.latest_summary(frame)
        self.assertEqual(latest["percentile"], 25)
        table = model.scenario_table(latest, [("P50", 15)], -20)
        self.assertAlmostEqual(table["对应价格"].iloc[0], 108)
        self.assertAlmostEqual(table["相对参考收盘价（%）"].iloc[0], 20)

    def test_latest_loss_or_missing_does_not_show_older_pe_as_current(self):
        for pe in (-3, 0, None, float("inf")):
            f, _ = model.align_history([dict(date="2026-11-24", close=100), dict(date="2026-11-25", close=90)],
                                      [dict(date="2026-11-24", pe_ttm=20), dict(date="2026-11-25", pe_ttm=pe)], start=START, end=END)
            latest = model.latest_summary(f)
            self.assertEqual(latest["date"], "2026-11-25")
            self.assertFalse(latest["usable"])
            self.assertIsNone(latest["percentile"])
            self.assertTrue(model.scenario_table(latest, [("20x", 20)]).empty)

    def test_extreme_positive_pe_retained_and_bad_numbers_excluded(self):
        prices = [dict(date=f"2026-11-{d}", close=100) for d in (20, 23, 24, 25)]
        vals = [dict(date=p["date"], pe_ttm=v) for p, v in zip(prices, [20, 10000, "--", "nan"])]
        f, c = model.align_history(prices, vals, start=START, end=END)
        self.assertEqual(c["positive_days"], 2)
        self.assertEqual(model.band_multiples(f)[2][1], 5010)

    def test_split_same_day_identity_and_same_currency(self):
        # A hypothetical 2:1 split halves price and per-share earnings; PE stays 20.
        f, _ = model.align_history([dict(date="2026-11-24", close=100), dict(date="2026-11-25", close=50)],
                                  [dict(date="2026-11-24", pe_ttm=20), dict(date="2026-11-25", pe_ttm=20)], start=START, end=END)
        np.testing.assert_allclose(model.band_prices(f, [("20x", 20)])["20x"], f.close)
        self.assertEqual(f.implied_eps.tolist(), [5, 2.5])

    def test_conflicting_duplicates_rejected_and_future_rows_excluded(self):
        with self.assertRaises(ValueError):
            model.clean_history([dict(date="2026-11-25", close=100), dict(date="2026-11-25", close=101)], ("close",))
        f, _ = model.align_history([dict(date="2026-11-25", close=100), dict(date="2026-11-26", close=999)],
                                  [dict(date="2026-11-25", pe_ttm=20), dict(date="2026-11-26", pe_ttm=1)], start=START, end=END)
        self.assertEqual(len(f), 1)

    def test_local_publication_cutoff_with_dst_and_leap_year(self):
        stamp = dt.datetime(2026, 11, 26, 7, tzinfo=dt.timezone.utc)
        self.assertEqual(model.completed_cutoff("A_SHARE", stamp).isoformat(), "2026-11-25")
        self.assertEqual(model.completed_cutoff("US_SHARE", stamp).isoformat(), "2026-11-25")
        self.assertEqual(model.completed_cutoff("HK_SHARE", stamp.replace(hour=10)).isoformat(), "2026-11-26")
        self.assertEqual(model.completed_cutoff("US_SHARE", dt.datetime(2026, 7, 8, 21, tzinfo=dt.timezone.utc)).isoformat(), "2026-07-07")
        self.assertEqual(model.window_start(dt.date(2024, 2, 29), 1), dt.date(2023, 2, 28))

    def test_explicit_equities_only_and_no_share_class_truncation(self):
        from page_live_quote import resolve_symbol
        self.assertEqual(resolve_symbol("BRK.B")["tx_code"], "usBRK.B")
        self.assertEqual(resolve_symbol("AAPL.OQ")["tx_code"], "usAAPL")
        self.assertEqual(resolve_symbol("SPY.US")["tx_code"], "usSPY")
        for raw in ("600519", "00700", "AAPL", "bj920002"):
            model.equity_identity(resolve_symbol(raw))
        for raw in ("sh000001", "513100", "SPY", "HSI", "HF_GC", "hk83188"):
            with self.assertRaises(ValueError, msg=raw):
                model.equity_identity(resolve_symbol(raw))

    def test_custom_multiples_validation(self):
        f, _ = fixture()
        self.assertEqual([v for _, v in model.band_multiples(f, "30，10,20,10")], [10, 20, 30])
        for text in ("10", "10,0", "10,-1", "nan,10", "inf,10", "abc,10"):
            with self.assertRaises(ValueError):
                model.band_multiples(f, text)


class SourceTests(unittest.TestCase):
    def test_eastmoney_exchange_and_pagination(self):
        def payload(date):
            return dict(result=dict(pages=2, data=[dict(SECUCODE="600519.SH", TRADE_DATE=date, CLOSE_PRICE=100, PE_TTM=20)]))
        replies = [Mock(json=Mock(return_value=payload(day))) for day in ("2026-11-25", "2026-11-24")]
        seen = []
        def get(url, params):
            seen.append(copy.deepcopy(params))
            return replies.pop(0)
        with patch.object(data, "_get", side_effect=get):
            self.assertEqual(len(data.fetch_a_history("sh600519", START, END)), 2)
        self.assertEqual(seen[1]["pageNumber"], 2)
        self.assertIn('600519.SH', seen[0]["filter"])
        with self.assertRaises(ValueError):
            data.parse_eastmoney(payload("2026-11-25"), "sz600519")

    def test_baidu_exact_period_ttm_and_identity(self):
        chart = dict(type="近三年", header=["市盈率(TTM)"], dimensions=["date", "curTagValue"], body=[["2026-11-25", "20"]])
        payload = dict(Result=[dict(DisplayData=dict(resultData=dict(tplData=dict(result=dict(code="00700", chartInfo=[chart])))))])
        self.assertEqual(data.parse_baidu(payload, "00700", "近三年")[0]["pe_ttm"], "20")
        for code, period in [("00981", "近三年"), ("00700", "近五年")]:
            with self.assertRaises(ValueError):
                data.parse_baidu(payload, code, period)
        chart["header"] = ["市盈率(静)"]
        with self.assertRaises(ValueError):
            data.parse_baidu(payload, "00700", "近三年")

    def test_sina_never_executes_remote_js_or_wrong_symbol(self):
        for text in ('var KLC_K2_MSFT="abc";', 'var KLC_K2_AAPL="abc"; evil();'):
            with self.assertRaises(ValueError):
                data.decode_sina(text, "AAPL", "US_SHARE")
        with patch("py_mini_racer.MiniRacer") as mini:
            js = mini.return_value.__enter__.return_value
            js.call.return_value = [dict(date="2026-11-25", close=100)]
            rows = data.decode_sina('var KLC_K2_00700="abcd"; /* source checksum */', "00700", "HK_SHARE")
            self.assertEqual(rows[0]["close"], 100)
            self.assertNotIn("source checksum", js.eval.call_args.args[0])


class PageTests(unittest.TestCase):
    def page(self, data_snapshot=None, error=""):
        import page_pe_band as page
        with patch.object(page, "_load", return_value=(data_snapshot or snapshot(), error)):
            at = AppTest.from_string("from page_pe_band import render_pe_band\nrender_pe_band({'market':'HK_SHARE','tx_code':'hk00700'})", default_timeout=15).run()
        self.assertFalse(at.exception)
        return at

    def test_page_band_chart_scenario_and_source_currency(self):
        at = self.page()
        self.assertEqual(len(at.get("plotly_chart")), 1)
        self.assertEqual(at.metric[0].value, "90.00")
        self.assertEqual(at.metric[2].value, "25.0%")
        self.assertEqual(len(at.dataframe[0].value), 5)
        self.assertIn("HKD", at.metric[0].label)
        import page_pe_band as page
        with patch.object(page, "_load", return_value=(snapshot(), "")):
            at.slider[0].set_value(-20).run()
        self.assertFalse(at.exception)
        self.assertAlmostEqual(at.dataframe[0].value["对应价格"].iloc[2], 108)

    def test_page_no_latest_pe_has_no_current_scenario(self):
        snap = snapshot()
        snap["frame"].loc[3, ["pe_ttm", "implied_eps"]] = np.nan
        snap["frame"].loc[3, "usable"] = False
        at = self.page(snap)
        self.assertEqual(len(at.slider), 0)
        self.assertEqual(at.metric[2].value, "—")

    def test_page_data_failure_no_fake_chart(self):
        at = self.page(error="来源暂时不可用")
        self.assertTrue(at.warning)
        self.assertEqual(len(at.get("plotly_chart")), 0)


if __name__ == "__main__":
    unittest.main()

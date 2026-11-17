"""Producer-side checks: no network, only archive plumbing and basic-table fallbacks."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import daily_convertible as dc


class FakePro:
    """cb_basic that only answers the minimal field list, like a lower-tier account."""

    def __init__(self, *, minimal_only: bool = True, fail_all: bool = False):
        self.minimal_only = minimal_only
        self.fail_all = fail_all
        self.fields_seen: list[str] = []

    def cb_basic(self, fields: str = ""):
        self.fields_seen.append(fields)
        if self.fail_all:
            raise RuntimeError("token=secret")
        if self.minimal_only and "list_date" in fields:
            raise RuntimeError("unknown field")
        return pd.DataFrame([{
            "ts_code": "111001.SH", "bond_short_name": "甲转债", "stk_code": "600001.SH",
            "stk_short_name": "甲公司", "list_date": "20200101", "delist_date": None,
            "conv_price": 10.0, "remain_size": 2.5,
        }])


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._saved = (dc.BASIC_JSON, dc.BOND_CSV, dc.STOCK_CSV, dc.CALENDAR, dc.OUTPUT)
        dc.BASIC_JSON = self.root / "basic.json"
        dc.BOND_CSV = self.root / "bond_daily.csv"
        dc.STOCK_CSV = self.root / "stock_daily.csv"
        dc.CALENDAR = self.root / "trading_calendar.json"
        dc.OUTPUT = self.root / "convertible.json"

    def tearDown(self):
        dc.BASIC_JSON, dc.BOND_CSV, dc.STOCK_CSV, dc.CALENDAR, dc.OUTPUT = self._saved
        self.tmp.cleanup()

    def test_basic_falls_back_to_the_minimal_field_list(self):
        pro = FakePro()
        basic, _ = dc._load_basic(pro, "20261116", [])
        self.assertEqual(len(pro.fields_seen), 2)
        self.assertIn("remain_size", pro.fields_seen[1])
        self.assertNotIn("list_date", pro.fields_seen[1])
        self.assertEqual(basic["111001.SH"]["stk_code"], "600001.SH")
        self.assertTrue(dc.BASIC_JSON.exists())

    def test_basic_failure_falls_back_to_the_cached_table(self):
        dc.BASIC_JSON.write_text(json.dumps({"rows": [{
            "ts_code": "111001.SH", "bond_short_name": "甲转债", "stk_code": "600001.SH",
            "conv_price": 10.0}]}, ensure_ascii=False), encoding="utf-8")
        errors: list[str] = []
        basic, _ = dc._load_basic(FakePro(fail_all=True), "20261116", errors)
        self.assertEqual(basic["111001.SH"]["bond_short_name"], "甲转债")
        self.assertIn("cb_basic_unavailable", errors)

    def test_api_errors_are_recorded_as_categories_only(self):
        errors: list[str] = []
        dc._load_basic(FakePro(fail_all=True), "20261116", errors)
        self.assertEqual(errors, ["cb_basic_unavailable"] * 2)
        self.assertNotIn("secret", " ".join(errors))

    def test_basic_map_skips_delisted_and_retired_bonds(self):
        frame = pd.DataFrame([
            {"ts_code": "111001.SH", "stk_code": "600001.SH", "delist_date": "20260101",
             "remain_size": 2.0},
            {"ts_code": "111002.SH", "stk_code": "600002.SH", "delist_date": "20270101",
             "remain_size": 0.0},
            {"ts_code": "111003.SH", "stk_code": "600003.SH", "delist_date": "20270101",
             "remain_size": 3.0},
        ])
        basic = dc._basic_map(frame, "20261116")
        self.assertEqual(set(basic), {"111003.SH"})

    def test_archive_merge_dedupes_and_keeps_the_newest_dates(self):
        frame = pd.DataFrame(columns=dc.BOND_COLUMNS)
        for date in ("20260101", "20260102"):
            rows = pd.DataFrame([{"date": date, "ts_code": "111001.SH", "close": 100.0}])
            for column in dc.BOND_COLUMNS:
                if column not in rows.columns:
                    rows[column] = None
            frame = dc._merge_archive(frame, rows, dc.BOND_COLUMNS)
        again = pd.DataFrame([{"date": "20260102", "ts_code": "111001.SH", "close": 101.0}])
        for column in dc.BOND_COLUMNS:
            if column not in again.columns:
                again[column] = None
        frame = dc._merge_archive(frame, again, dc.BOND_COLUMNS)
        self.assertEqual(len(frame), 2)
        latest = frame[frame["date"] == "20260102"].iloc[0]
        self.assertEqual(latest["close"], 101.0)

    def test_open_dates_reads_the_offline_calendar(self):
        dc.CALENDAR.write_text(json.dumps({"open_dates": ["20261113", "20261116"]}),
                               encoding="utf-8")
        self.assertEqual(dc.open_dates(), ["20261113", "20261116"])
        dc.CALENDAR.write_text("{bad json", encoding="utf-8")
        self.assertEqual(dc.open_dates(), [])


if __name__ == "__main__":
    unittest.main()

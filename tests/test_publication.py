import ast
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import pandas as pd
import streamlit as st

from publication_status import publication_status
from daily_industry_signal import target_trade_date
from daily_trading_calendar import build_calendar
from update_schedule import CST, MAIN_CRON_CST
import tools_gh_watchdog as watchdog

ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def status(self, hour, minute=0, dates=None, day=16, calendar=None):
        now = dt.datetime(2026, 11, day, hour, minute, tzinfo=CST)
        return publication_status(dates or {"市场": "20261113", "RPS": "20261113"},
                                            now, calendar)

    def test_monday_previous_friday_is_late_at_seven(self):
        result = self.status(19)
        self.assertEqual(result["status"], "delayed")
        self.assertEqual(result["expected_date"], "20261116")
        self.assertEqual(result["stale"], ["市场", "RPS"])

    def test_publication_window_and_timezone(self):
        self.assertEqual(self.status(18, 36)["status"], "scheduled")
        self.assertEqual(self.status(18, 37)["status"], "waiting")
        self.assertEqual(self.status(18, 59)["status"], "waiting")
        result = publication_status({"市场": "20261113"},
                    dt.datetime(2026, 11, 16, 11, tzinfo=dt.timezone.utc))
        self.assertEqual(result["status"], "delayed")
        self.assertEqual(result["checked_at"], "2026-11-16 19:00")

    def test_fresh_module_cannot_hide_stale_or_unknown_module(self):
        result = self.status(19, dates={"市场": "20261116", "RPS": "20261113", "板块": None})
        self.assertEqual(result["status"], "delayed")
        self.assertEqual(result["stale"], ["RPS"])
        self.assertEqual(result["unknown"], ["板块"])
        self.assertEqual(self.status(19, dates={"市场": "20261116"})["status"], "current")
        self.assertEqual(self.status(19, dates={"市场": "20261117"})["status"], "unknown")
        self.assertEqual(self.status(18, dates={"市场": "20261116", "RPS": "20261113"})["stale"], ["RPS"])

    def test_weekend_and_exchange_holiday_are_not_delayed(self):
        calendar = {"start_date": "20261101", "end_date": "20261130",
                    "open_dates": ["20261112", "20261113", "20261116"]}
        result = self.status(19, day=15, calendar=calendar)
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["expected_date"], "20261113")
        holiday = {"start_date": "20260901", "end_date": "20261031",
                   "open_dates": ["20260929", "20260930", "20261009"]}
        result = publication_status({"市场": "20260930"}, dt.datetime(2026, 10, 5, 19), holiday)
        self.assertEqual(result["status"], "current")
        self.assertIn("今日休市", result["message"])

    def test_missing_or_expired_calendar_is_explicit(self):
        for calendar in (None, {}, {"start_date": "20250101", "end_date": "20251231",
                                   "open_dates": ["20250102"]}):
            result = self.status(19, day=15, calendar=calendar)
            self.assertFalse(result["calendar_known"])
            self.assertIn("估算", result["message"])
            self.assertNotEqual(result["status"], "delayed")

    def test_industry_uses_today_at_source_publication_boundary(self):
        pro = Mock()
        def trade_cal(**kwargs):
            days = [d for d in ("20261113", "20261116") if d <= kwargs["end_date"]]
            return pd.DataFrame({"cal_date": days})
        pro.trade_cal.side_effect = trade_cal
        self.assertEqual(target_trade_date(pro, dt.datetime(2026, 11, 16, 18, 29)), "20261113")
        self.assertEqual(target_trade_date(pro, dt.datetime(2026, 11, 16, 18, 30)), "20261116")
        self.assertEqual(target_trade_date(pro, dt.datetime(2026, 11, 16, 18, 37)), "20261116")

    def test_schedule_matches_watchdog_and_ui_window(self):
        import re
        workflow = (ROOT / ".github/workflows/daily_update.yml").read_text(encoding="utf-8")
        minute, hour = map(int, re.search(r"cron: '(\d+) (\d+)", workflow).groups())
        self.assertEqual(((hour + 8) % 24, minute), MAIN_CRON_CST)
        self.assertEqual(watchdog.judge_after(dt.datetime(2026, 11, 16, tzinfo=CST)),
                         dt.datetime(2026, 11, 16, 19, 30, tzinfo=CST))
        self.assertIn('cron: "35 11', (ROOT / ".github/workflows/watchdog.yml").read_text(encoding="utf-8"))


class CalendarTests(unittest.TestCase):
    def test_complete_exchange_calendar_and_incomplete_rejection(self):
        days = pd.date_range("2025-01-01", "2026-12-31")
        frame = pd.DataFrame({"cal_date": days.strftime("%Y%m%d"), "is_open": (days.weekday < 5).astype(int)})
        frame.loc[frame.cal_date == "20261001", "is_open"] = 0
        pro = Mock()
        pro.trade_cal.return_value = frame
        result = build_calendar(pro, dt.datetime(2026, 11, 16, 19))
        self.assertNotIn("20261001", result["open_dates"])
        self.assertIn("20261116", result["open_dates"])
        self.assertEqual(pro.trade_cal.call_args.kwargs["exchange"], "SSE")
        for invalid in (frame.iloc[:-1], pd.DataFrame(), frame.assign(is_open="unknown")):
            pro.trade_cal.return_value = invalid
            with self.assertRaises(ValueError):
                build_calendar(pro, dt.datetime(2026, 11, 16, 19))


class DataCacheTests(unittest.TestCase):
    def test_actual_loaders_notice_changed_file_within_ttl(self):
        # Execute the real cached functions without launching the app or making network calls.
        source = ROOT / "app.py"
        names = {"_data_file", "_read_csv", "_read_json", "load_data", "load_json"}
        tree = ast.parse(source.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {"__file__": str(source), "st": st, "os": os, "pd": pd, "json": json}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
        with tempfile.TemporaryDirectory() as folder:
            csv_path = Path(folder) / "data.csv"
            json_path = Path(folder) / "data.json"
            for path, loader, first, second in (
                (csv_path, namespace["load_data"], "date\n20261113\n", "date\n20261116\n"),
                (json_path, namespace["load_json"], '{"date":"20261113"}', '{"date":"20261116"}'),
            ):
                self.assertIsNone(loader(str(path)))
                path.write_text(first, encoding="utf-8")
                original = loader(str(path))
                stamp = path.stat().st_mtime_ns
                path.write_text(second, encoding="utf-8")
                os.utime(path, ns=(stamp + 1000000000, stamp + 1000000000))
                updated = loader(str(path))
                old_date = str(original["date"].iloc[0]) if isinstance(original, pd.DataFrame) else original["date"]
                new_date = str(updated["date"].iloc[0]) if isinstance(updated, pd.DataFrame) else updated["date"]
                self.assertEqual(old_date, "20261113")
                self.assertEqual(new_date, "20261116")
        namespace["_read_csv"].clear()
        namespace["_read_json"].clear()

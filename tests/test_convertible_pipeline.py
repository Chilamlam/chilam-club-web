"""End-to-end run of the producer with a fake Tushare, no network and no real data files."""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

import daily_convertible as dc

BONDS = {
    "111001.SH": ("600001.SH", 10.0),
    "111002.SH": ("600002.SH", 12.0),
    "111003.SH": ("600003.SH", 8.0),
}


def _dates(count: int) -> list[str]:
    out, day = [], dt.date(2026, 9, 1)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.strftime("%Y%m%d"))
        day += dt.timedelta(days=1)
    return out


class FakePro:
    def __init__(self, dates: list[str]):
        self.dates = dates
        self.calls: list[str] = []

    def cb_basic(self, fields: str = ""):
        rows = []
        for code, (stock, conv) in BONDS.items():
            rows.append({"ts_code": code, "bond_short_name": f"债{code[:3]}",
                         "stk_code": stock, "stk_short_name": f"股{stock[:3]}",
                         "list_date": "20200101", "delist_date": None,
                         "conv_price": conv, "remain_size": 2.0})
        return pd.DataFrame(rows)

    def cb_daily(self, trade_date: str = ""):
        self.calls.append(f"cb_daily:{trade_date}")
        if trade_date not in self.dates:
            return pd.DataFrame()
        step = self.dates.index(trade_date)
        rows = []
        for offset, (code, (stock, conv)) in enumerate(BONDS.items()):
            close = 110.0 + offset * 5 + step * (1.0 + offset)
            rows.append({"ts_code": code, "open": close - 0.5, "high": close + 1.0,
                         "low": close - 1.0, "close": close, "pre_close": close - 1.0,
                         "pct_chg": (close / (close - 1.0) - 1) * 100,
                         "amount": 120_000.0 + offset * 40_000})
        return pd.DataFrame(rows)

    def daily(self, trade_date: str = ""):
        self.calls.append(f"daily:{trade_date}")
        if trade_date not in self.dates:
            return pd.DataFrame()
        step = self.dates.index(trade_date)
        rows = []
        for code, (stock, conv) in BONDS.items():
            close = conv * 11 + step * 0.5
            rows.append({"ts_code": stock, "open": close - 0.2, "high": close + 0.3,
                         "low": close - 0.4, "close": close, "pre_close": close - 0.3,
                         "pct_chg": 3.5})
        rows.append({"ts_code": "000001.SZ", "open": 10, "high": 10, "low": 10,
                     "close": 10, "pre_close": 10, "pct_chg": 0.0})
        return pd.DataFrame(rows)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "data").mkdir()
        self._saved = {name: getattr(dc, name) for name in
                       ("ARCHIVE_DIR", "BOND_CSV", "STOCK_CSV", "BASIC_JSON", "OUTPUT", "CALENDAR")}
        dc.ARCHIVE_DIR = root / "data" / "convertible"
        dc.BOND_CSV = dc.ARCHIVE_DIR / "bond_daily.csv"
        dc.STOCK_CSV = dc.ARCHIVE_DIR / "stock_daily.csv"
        dc.BASIC_JSON = dc.ARCHIVE_DIR / "basic.json"
        dc.OUTPUT = root / "data" / "convertible.json"
        dc.CALENDAR = root / "data" / "trading_calendar.json"
        self.dates = _dates(12)
        dc.CALENDAR.write_text(json.dumps({"open_dates": self.dates}), encoding="utf-8")
        self.pro = FakePro(self.dates)
        self._saved_ts = sys.modules.get("tushare")
        sys.modules["tushare"] = types.SimpleNamespace(
            pro_api=lambda token, timeout=0: self.pro)  # type: ignore[arg-type]
        self._saved_env = dc.os.environ.get("TUSHARE_TOKEN")
        dc.os.environ["TUSHARE_TOKEN"] = "test-token"

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(dc, name, value)
        if self._saved_ts is None:
            sys.modules.pop("tushare", None)
        else:
            sys.modules["tushare"] = self._saved_ts
        if self._saved_env is None:
            dc.os.environ.pop("TUSHARE_TOKEN", None)
        else:
            dc.os.environ["TUSHARE_TOKEN"] = self._saved_env
        self.tmp.cleanup()

    def test_first_run_bootstraps_the_archive_and_writes_a_snapshot(self):
        self.assertEqual(dc.main(), 0)
        payload = json.loads(dc.OUTPUT.read_text(encoding="utf-8"))
        self.assertEqual(payload["date"], self.dates[-1])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(sum(payload["classes"][key]["count"] for key in "ABCN"), len(BONDS))
        self.assertGreaterEqual(payload["archive_days"], 11)
        self.assertTrue(dc.BOND_CSV.exists() and dc.STOCK_CSV.exists())
        self.assertTrue(any(call.startswith("daily:") for call in self.pro.calls))

    def test_second_run_reuses_the_archive_instead_of_refetching(self):
        dc.main()
        first = len(self.pro.calls)
        self.pro.calls.clear()
        self.assertEqual(dc.main(), 0)
        self.assertLess(len(self.pro.calls), first)
        self.assertLessEqual(len([c for c in self.pro.calls if c.startswith("daily:")]), 1)

    def test_missing_token_keeps_the_previous_snapshot_and_says_so(self):
        dc.OUTPUT.write_text(json.dumps({"date": "20261116", "status": "ok",
                                         "classes": {}, "market": {}, "patterns": {},
                                         "watch": {}, "notes": []}), encoding="utf-8")
        dc.os.environ.pop("TUSHARE_TOKEN", None)
        self.assertEqual(dc.main(), 0)
        payload = json.loads(dc.OUTPUT.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["date"], "20261116")

    def test_unexpected_exception_still_writes_a_status(self):
        dc.os.environ["TUSHARE_TOKEN"] = "test-token"
        sys.modules["tushare"] = types.SimpleNamespace(
            pro_api=lambda token, timeout=0: (_ for _ in ()).throw(RuntimeError("boom")))
        dc.OUTPUT.write_text(json.dumps({"date": "20261116", "status": "ok", "classes": {},
                                         "market": {}, "patterns": {}, "watch": {},
                                         "notes": []}), encoding="utf-8")
        self.assertEqual(dc.main(), 1)
        payload = json.loads(dc.OUTPUT.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "error")
        self.assertIn("RuntimeError", payload["status_message"])


if __name__ == "__main__":
    unittest.main()

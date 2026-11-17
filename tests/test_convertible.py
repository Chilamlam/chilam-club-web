"""Pure-logic checks for the convertible-bond ranking and review statistics."""
from __future__ import annotations

import datetime as dt
import unittest

import pandas as pd

import convertible


def _dates(count: int) -> list[str]:
    """Consecutive weekdays; the module only needs increasing YYYYMMDD strings."""
    out, day = [], dt.date(2026, 9, 1)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.strftime("%Y%m%d"))
        day += dt.timedelta(days=1)
    return out


def _bar(date, code, *, close=100.0, open_=None, high=None, low=None,
         pct=0.0, amount=200_000.0) -> dict:
    open_ = close if open_ is None else open_
    high = max(close, open_) if high is None else high
    low = min(close, open_) if low is None else low
    return {"date": date, "ts_code": code, "open": open_, "high": high, "low": low,
            "close": close, "pre_close": close, "pct_chg": pct, "amount": amount}


def _basic(**overrides) -> dict:
    base = {"bond_short_name": "测试转债", "stk_code": "600000.SH", "stk_short_name": "测试股份",
            "conv_price": 10.0, "remain_size": 50000.0, "list_date": "20200101",
            "delist_date": None}
    base.update(overrides)
    return base


def _build(bond_rows, stock_rows, basic, trade_date, stat_days=10):
    bond = pd.DataFrame(bond_rows)
    stock = pd.DataFrame(stock_rows)
    return convertible.build_snapshot(bond, stock, basic, trade_date, stat_days=stat_days)


class ClassifyTests(unittest.TestCase):
    def test_bond_driven_move_is_a(self):
        self.assertEqual(convertible.classify(3.0, 0.2), "A")

    def test_bond_strong_despite_falling_stock_is_a(self):
        self.assertEqual(convertible.classify(-0.5, -8.0), "A")

    def test_stock_driven_move_is_b(self):
        self.assertEqual(convertible.classify(0.5, 9.0), "B")

    def test_same_direction_comparable_move_is_c(self):
        self.assertEqual(convertible.classify(4.0, 5.0), "C")

    def test_flat_pair_is_neutral(self):
        self.assertEqual(convertible.classify(0.2, 0.3), "N")

    def test_missing_leg_is_neutral(self):
        self.assertEqual(convertible.classify(None, 5.0), "N")
        self.assertEqual(convertible.classify(5.0, float("nan")), "N")

    def test_threshold_edges(self):
        self.assertEqual(convertible.classify(1.5, 0.0), "A")   # exactly A_GAP
        self.assertEqual(convertible.classify(1.49, 0.0), "N")  # just below
        self.assertEqual(convertible.classify(2.0, 0.0), "A")   # stock flat -> bond-driven
        self.assertEqual(convertible.classify(0.0, 2.0), "B")

    def test_c_needs_enough_bond_follow_through(self):
        # Stock +9%, bond +1%: the bond lags far behind -> B, not C.
        self.assertEqual(convertible.classify(1.0, 9.0), "B")
        # Stock +2%, bond +1%: same direction and 50% follow-through -> C.
        self.assertEqual(convertible.classify(1.0, 2.0), "C")


class SnapshotTests(unittest.TestCase):
    def test_partition_is_complete_and_stock_pct_attached(self):
        dates = _dates(6)
        bond, stock = [], []
        specs = {
            "111001.SH": ("600001.SH", 8.0, 0.2, "A"),
            "111002.SH": ("600002.SH", 0.4, 9.0, "B"),
            "111003.SH": ("600003.SH", 4.0, 5.0, "C"),
            "111004.SH": ("600004.SH", 0.1, 0.2, "N"),
        }
        for date in dates:
            for code, (stk, bond_pct, stock_pct, _) in specs.items():
                bond.append(_bar(date, code, pct=bond_pct if date == dates[-1] else 0.0))
                stock.append(_bar(date, stk, pct=stock_pct if date == dates[-1] else 0.0))
        basic = {code: _basic(stk_code=stk) for code, (stk, *_) in specs.items()}

        payload = _build(bond, stock, basic, dates[-1])
        counts = {key: payload["classes"][key]["count"] for key in ("A", "B", "C", "N")}
        self.assertEqual(counts, {"A": 1, "B": 1, "C": 1, "N": 1})
        labels = {row["code"]: row["class"] for row in payload["classes"]["A"]["rows"]}
        self.assertEqual(labels, {"111001.SH": "A"})
        self.assertEqual(payload["market"]["total"], 4)

    def test_premium_and_double_low_come_from_latest_conversion_price(self):
        dates = _dates(3)
        bond = [_bar(d, "111001.SH", close=120.0) for d in dates]
        stock = [_bar(d, "600001.SH", close=12.0) for d in dates]
        payload = _build(bond, stock, {"111001.SH": _basic(conv_price=10.0,
                                                          stk_code="600001.SH")}, dates[-1])
        row = payload["classes"]["N"]["rows"][0]
        # conversion value = 100 / 10 * 12 = 120 -> premium 0%
        self.assertAlmostEqual(row["premium"], 0.0, places=6)
        self.assertAlmostEqual(row["double_low"], 120.0, places=6)

    def test_missing_trade_date_is_an_error_not_an_empty_page(self):
        with self.assertRaises(ValueError):
            _build([_bar("20260901", "111001.SH")], [], {}, "20260902")

    def test_non_finite_numbers_are_dropped(self):
        dates = _dates(2)
        bond = [_bar(d, "111001.SH") for d in dates]
        stock = [_bar(d, "600001.SH", close=float("nan")) for d in dates]
        payload = _build(bond, stock, {"111001.SH": _basic()}, dates[-1])
        row = payload["classes"]["N"]["rows"][0]
        self.assertIsNone(row["premium"])
        self.assertEqual(row["class"], "N")


class PatternTests(unittest.TestCase):
    def _ten_days(self):
        dates = _dates(11)
        bond, stock = [], []
        close = 100.0
        last = len(dates) - 1
        for index, date in enumerate(dates):
            if index == last:
                open_ = close * 1.02
                low, high, close_now = close * 1.01, close * 1.04, close * 1.03
            else:
                open_ = close * 1.005
                low, high, close_now = open_ * 0.99, open_ * 1.01, open_
            bond.append(_bar(date, "111001.SH", close=close_now, open_=open_, low=low,
                             high=high, pct=(close_now / close - 1) * 100))
            stock.append(_bar(date, "600001.SH", close=10.0, pct=0.2))
            close = close_now
        return dates, bond, stock

    def test_overnight_open_and_dip_returns_use_next_session_bars(self):
        dates, bond, stock = self._ten_days()
        payload = _build(bond, stock, {"111001.SH": _basic()}, dates[-1])
        rows = {row["date"]: row for row in payload["patterns"]["rows"]}
        yesterday = dates[-2]
        row = rows[yesterday]
        self.assertAlmostEqual(row["overnight"], 2.0, places=6)
        self.assertAlmostEqual(row["open_to_close"], (103 / 102 - 1) * 100, places=6)
        self.assertAlmostEqual(row["low_to_close"], (103 / 101 - 1) * 100, places=6)
        self.assertAlmostEqual(row["next_high"], 4.0, places=6)

    def test_aggregate_reports_sample_size_and_win_rate(self):
        dates, bond, stock = self._ten_days()
        payload = _build(bond, stock, {"111001.SH": _basic()}, dates[-1])
        rank1 = payload["patterns"]["aggregate"]["rank1"]
        self.assertEqual(rank1["n"], 10)
        self.assertEqual(rank1["overnight"]["n"], 10)
        self.assertAlmostEqual(rank1["overnight"]["win"], 100.0, places=6)

    def test_latest_day_has_no_outcome_yet(self):
        dates, bond, stock = self._ten_days()
        payload = _build(bond, stock, {"111001.SH": _basic()}, dates[-1])
        self.assertNotIn(dates[-1], {row["date"] for row in payload["patterns"]["rows"]})

    def test_illiquid_bond_never_becomes_a_leader(self):
        dates = _dates(4)
        bond, stock = [], []
        for date in dates:
            bond.append(_bar(date, "111001.SH", pct=20.0, amount=1_000.0))
            bond.append(_bar(date, "111002.SH", pct=2.0, amount=200_000.0))
            stock.append(_bar(date, "600001.SH"))
            stock.append(_bar(date, "600002.SH"))
        payload = _build(bond, stock,
                         {"111001.SH": _basic(stk_code="600001.SH"),
                          "111002.SH": _basic(stk_code="600002.SH")}, dates[-1])
        codes = {row["code"] for row in payload["patterns"]["rows"]}
        self.assertNotIn("111001.SH", codes)
        self.assertIn("111002.SH", codes)

    def test_stage_rows_only_come_from_the_linked_family(self):
        dates = _dates(12)
        bond, stock = [], []
        for index, date in enumerate(dates):
            # Rising linked pair: the bond follows the stock every day.
            price = 100.0 * (1.06 ** index)
            bond.append(_bar(date, "111003.SH", close=price, pct=6.0, amount=200_000.0))
            stock.append(_bar(date, "600003.SH", close=price, pct=7.0))
            # Bond-only spike, no stock participation -> A, never staged.
            bond.append(_bar(date, "111001.SH", pct=8.0, amount=200_000.0))
            stock.append(_bar(date, "600001.SH", pct=0.2))
        payload = _build(bond, stock, {"111003.SH": _basic(stk_code="600003.SH"),
                                       "111001.SH": _basic(stk_code="600001.SH")}, dates[-1])
        codes = {row["code"] for row in payload["patterns"]["stage_rows"]}
        self.assertEqual(codes, {"111003.SH"})
        self.assertIn("加速", {row["stage"] for row in payload["patterns"]["stage_rows"]})


class SubTagTests(unittest.TestCase):
    def test_new_listing_high_premium_and_volume_are_separate_labels(self):
        tag = convertible.sub_tag(list_date="20261001", trade_date="20261116",
                                  premium=35.0, amount_ratio=2.0)
        self.assertEqual(tag, "次新+高溢价+放量")

    def test_quiet_old_bond_has_no_sub_tag(self):
        self.assertEqual(convertible.sub_tag(list_date="20200101", trade_date="20261116",
                                             premium=10.0, amount_ratio=1.0), "")


if __name__ == "__main__":
    unittest.main()

"""Render checks for the convertible-bond board (no network, no real data files)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]

PAYLOAD = {
    "date": "20261116",
    "generated_at": "2026-11-16T19:10:00+08:00",
    "status": "ok",
    "market": {"total": 4, "up": 3, "down": 1, "flat": 0, "median_pct": 1.2,
               "avg_double_low": 150.0, "avg_premium": 20.0, "amount_yi": 30.0, "liquid": 4},
    "classes": {
        "A": {"label": "A 债强于股（不联动）", "count": 1, "rows": [{
            "code": "111001.SH", "name": "甲转债", "stock_code": "600001.SH",
            "stock_name": "甲公司", "stock_pct": 0.2, "close": 130.0, "pct_chg": 8.0,
            "amount_yi": 5.0, "premium": 35.0, "double_low": 165.0, "remain_size": 9000.0,
            "list_date": "20261001", "corr20": 0.1, "class": "A", "stage": "",
            "sub_tag": "次新+高溢价", "amount_ratio": 2.0}]},
        "B": {"label": "B 股强于债（不联动）", "count": 1, "rows": [{
            "code": "111002.SH", "name": "乙转债", "stock_code": "600002.SH",
            "stock_name": "乙公司", "stock_pct": 9.0, "close": 110.0, "pct_chg": 0.5,
            "amount_yi": 3.0, "premium": 12.0, "double_low": 122.0, "remain_size": 50000.0,
            "list_date": "20200101", "corr20": 0.6, "class": "B", "stage": "",
            "sub_tag": "", "amount_ratio": 1.0}]},
        "C": {"label": "C 股债联动", "count": 1, "rows": [{
            "code": "111003.SH", "name": "丙转债", "stock_code": "600003.SH",
            "stock_name": "丙公司", "stock_pct": 5.0, "close": 120.0, "pct_chg": 4.0,
            "amount_yi": 8.0, "premium": 5.0, "double_low": 125.0, "remain_size": 30000.0,
            "list_date": "20220101", "corr20": 0.7, "class": "C", "stage": "加速",
            "sub_tag": "", "amount_ratio": 1.8}]},
        "N": {"label": "N 中性/无明显分化", "count": 1, "rows": [{
            "code": "111004.SH", "name": "丁转债", "stock_code": "600004.SH",
            "stock_name": "丁公司", "stock_pct": 0.3, "close": 100.0, "pct_chg": 0.1,
            "amount_yi": 1.0, "premium": 20.0, "double_low": 120.0, "remain_size": 20000.0,
            "list_date": "20230101", "corr20": None, "class": "N", "stage": "",
            "sub_tag": "", "amount_ratio": 0.8}]},
    },
    "patterns": {
        "window": {"days": 10, "from": "20261102", "to": "20261113"},
        "aggregate": {
            "rank1": {"n": 10, "overnight": {"n": 10, "mean": 1.5, "median": 1.2, "win": 70.0},
                      "open_to_close": {"n": 10, "mean": -0.3, "median": -0.2, "win": 40.0},
                      "low_to_close": {"n": 10, "mean": 2.1, "median": 2.0, "win": 90.0},
                      "dip": {"n": 6, "total": 10, "mean": 0.8, "median": 0.7, "win": 50.0},
                      "close_to_close": {"n": 10, "mean": 1.2, "median": 1.0, "win": 70.0},
                      "next_high": {"n": 10, "mean": 3.4, "median": 3.0, "win": 90.0}},
            "rank2": {"n": 10, "overnight": {"n": 10, "mean": 0.9, "median": 0.8, "win": 60.0},
                      "open_to_close": {"n": 10, "mean": 0.2, "median": 0.1, "win": 50.0},
                      "low_to_close": {"n": 10, "mean": 1.7, "median": 1.6, "win": 80.0},
                      "dip": {"n": 4, "total": 10, "mean": -0.4, "median": -0.3, "win": 25.0},
                      "close_to_close": {"n": 10, "mean": 1.1, "median": 1.0, "win": 60.0},
                      "next_high": {"n": 10, "mean": 2.8, "median": 2.5, "win": 80.0}},
            "b_overnight": {"n": 4, "overnight": {"n": 4, "mean": 2.5, "median": 2.4, "win": 75.0},
                            "open_to_close": {"n": 4, "mean": 0.5, "median": 0.4, "win": 50.0},
                            "close_to_close": {"n": 4, "mean": 3.0, "median": 2.9, "win": 75.0}},
        },
        "rows": [{"date": "20261113", "next_date": "20261116", "rank": 1, "code": "111001.SH",
                  "pct_chg": 8.0, "amount_yi": 5.0, "overnight": 2.0, "open_to_close": 1.0,
                  "low_to_close": 3.0, "dip_to_close": 1.2, "dip_touch": True,
                  "close_to_close": 3.0, "next_high": 4.0, "next_pct": 3.0}],
        "swap": {"n": 10, "rate": 30.0},
        "b_rows": [{"date": "20261113", "next_date": "20261116", "code": "111002.SH",
                    "pct_chg": 0.5, "stock_pct": 9.0, "overnight": 2.5, "open_to_close": 0.5,
                    "low_to_close": 1.0, "close_to_close": 3.0, "next_high": 4.0, "next_pct": 3.0}],
        "stages": {name: {"n": 2, "next_pct": {"n": 2, "mean": 1.0, "median": 1.0, "win": 50.0},
                          "overnight": {"n": 2, "mean": 0.5, "median": 0.5, "win": 50.0},
                          "open_to_close": {"n": 2, "mean": 0.3, "median": 0.3, "win": 50.0},
                          "low_to_close": {"n": 2, "mean": 1.2, "median": 1.2, "win": 50.0}}
                   for name in ("退潮", "分歧", "高潮", "加速", "回流", "启动")},
        "stage_rows": [{"date": "20261113", "next_date": "20261116", "code": "111003.SH",
                        "stage": "加速", "pct_chg": 6.0, "overnight": 1.0, "open_to_close": 0.5,
                        "low_to_close": 1.5, "close_to_close": 1.5, "next_high": 2.0, "next_pct": 1.5}],
        "definitions": {"stage_rules": {name: "规则" for name in
                                        ("退潮", "分歧", "高潮", "加速", "回流", "启动")}},
    },
    "watch": {
        "today_leaders": [
            {"code": "111001.SH", "name": "甲转债", "pct_chg": 8.0, "stock_name": "甲公司",
             "stock_pct": 0.2, "premium": 35.0, "amount_yi": 5.0, "sub_tag": "次新"},
            {"code": "111003.SH", "name": "丙转债", "pct_chg": 4.0, "stock_name": "丙公司",
             "stock_pct": 5.0, "premium": 5.0, "amount_yi": 8.0, "sub_tag": ""},
        ],
        "yesterday": [{"code": "111001.SH", "name": "甲转债", "prev_pct": 8.0, "today_pct": 3.0,
                       "overnight": 2.0, "open_to_close": 1.0,
                       "dip_to_close": 1.2, "dip_touch": True}],
        "b_total": 4,
        "b_candidates": [{"code": "111002.SH", "name": "乙转债", "stock_name": "乙公司",
                          "stock_pct": 9.0, "pct_chg": 0.5, "premium": 12.0, "amount_yi": 3.0}],
    },
    "notes": ["分类只使用当日涨跌幅。"],
}


class ConvertiblePageTests(unittest.TestCase):
    def _run(self, payload: dict | None):
        source = (
            "import json\n"
            "from page_convertible import render_convertible_block\n"
            f"payload = json.loads({json.dumps(json.dumps(payload, ensure_ascii=False))})\n"
            "render_convertible_block(payload)\n"
        )
        app = AppTest.from_string(source, default_timeout=30)
        return app.run()

    def test_board_renders_every_tab_without_exception(self):
        app = self._run(PAYLOAD)
        self.assertEqual(list(app.exception), [])
        labels = [metric.label for metric in app.metric]
        self.assertIn("A 债强于股", labels)
        self.assertIn("C 股债同向候选", labels)
        self.assertIn("隔夜（昨收 → 次日开盘）", " ".join(m.value for m in app.markdown))
        self.assertEqual(len(app.tabs), 4)
        visible = " ".join([str(m.value) for m in app.markdown]
                           + [str(c.value) for c in app.caption] + labels)
        self.assertNotIn("胜率", visible)
        self.assertIn("正收益占比", visible)

    def test_class_radio_switches_the_table(self):
        app = self._run(PAYLOAD)
        radio = app.radio(key="cb_class_choice")
        self.assertEqual(radio.options, ["🅰️ 债强于股", "🅱️ 股强于债", "🔗 股债同向候选", "➖ 待进一步判断"])
        radio.set_value("➖ 待进一步判断").run()
        self.assertEqual(list(app.exception), [])
        names = [frame.value.iloc[0]["name"] for frame in app.dataframe if "name" in frame.value.columns]
        self.assertIn("丁转债", names)

    def test_missing_payload_warns_instead_of_crashing(self):
        app = self._run(None)
        self.assertEqual(list(app.exception), [])
        self.assertTrue(any("可转债快照尚未生成" in str(w.value) for w in app.warning))

    def test_failed_status_keeps_old_snapshot_with_a_warning(self):
        payload = dict(PAYLOAD)
        payload["status"] = "error"
        payload["status_message"] = "接口未返回数据"
        app = self._run(payload)
        self.assertEqual(list(app.exception), [])
        self.assertTrue(any("接口未返回数据" in str(w.value) for w in app.warning))
        self.assertTrue(any("20261116" in str(c.value) for c in app.caption))

    def test_snapshot_from_before_the_dip_column_still_renders(self):
        """发布顺序是代码先上、数据后补：旧快照不能让页面崩。"""
        payload = json.loads(json.dumps(PAYLOAD))
        for spec in payload["patterns"]["aggregate"].values():
            spec.pop("dip", None)
        for row in payload["patterns"]["rows"]:
            row.pop("dip_to_close", None)
            row.pop("dip_touch", None)
        for row in payload["watch"]["yesterday"]:
            row.pop("dip_to_close", None)
            row.pop("dip_touch", None)
        app = self._run(payload)
        self.assertEqual(list(app.exception), [])
        self.assertIn("回踩", " ".join(str(m.value) for m in app.markdown))


if __name__ == "__main__":
    unittest.main()

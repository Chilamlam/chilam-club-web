import unittest
import datetime as dt
import io
import json
from unittest.mock import Mock, patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from daily_ladder_themes import fetch_public_ths, fetch_sources
from ladder_themes import attribution_for, build_payload, parse_attributions, source_status_text
from update_schedule import CST


def ladder_dashboard(ladder, themes):
    import ast
    from pathlib import Path
    import pandas as pd
    import streamlit as st
    import data_freshness as freshness
    from ladder_themes import attribution_for, source_status_text
    from investor_journey import page_link, candidate_actions, WATCH_PAGE, REVIEW_PAGE

    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    dashboard = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == "render_market_dashboard")
    namespace = {
        "st": st, "pd": pd, "freshness": freshness,
        "attribution_for": attribution_for, "source_status_text": source_status_text,
        "load_data": lambda path: None,
        "load_json": lambda path: ladder if path.endswith("limit_ladder.json") else themes,
        "render_market_update_status": lambda *args: None,
        "render_sentiment_block": lambda: None,
        "page_link": page_link, "candidate_actions": candidate_actions,
        "WATCH_PAGE": WATCH_PAGE, "REVIEW_PAGE": REVIEW_PAGE,
    }
    exec(compile(ast.Module(body=[dashboard], type_ignores=[]), "app.py", "exec"), namespace)
    namespace["render_market_dashboard"]()


class LadderThemeTests(unittest.TestCase):
    def test_parse_requires_same_date_and_ladder_code(self):
        rows = [
            {"ts_code": "002148.SZ", "trade_date": "20261116", "lu_desc": "通信设备+卫星", "theme": "卫星互联网"},
            {"ts_code": "002148.SZ", "trade_date": "20261113", "lu_desc": "旧归因", "theme": "旧题材"},
            {"ts_code": "600000.SH", "trade_date": "20261116", "lu_desc": "不在天梯", "theme": "银行"},
        ]
        result = parse_attributions(rows, "kpl", "2026-11-16", ["002148"])
        self.assertEqual(set(result["records"]), {"002148"})
        self.assertEqual(result["records"]["002148"]["theme"], "卫星互联网")
        self.assertEqual(result["rejected_dates"], 1)

    def test_ths_reason_and_kpl_theme_are_merged(self):
        payload = {
            "date": "20261116",
            "sources": {
                "ths": {"records": {"002148": {"code": "002148", "date": "20261116",
                                                   "reason": "通信设备订单", "theme": ""}}},
                "kpl": {"records": {"002148": {"code": "002148", "date": "20261116",
                                                   "reason": "卫星链路", "theme": "卫星互联网、商业航天"}}},
            },
        }
        result = attribution_for("002148.SZ", "2026-11-16", payload)
        self.assertEqual(result["theme"], "卫星互联网、商业航天")
        self.assertIn("通信设备订单", result["reason"])
        self.assertIn("卫星链路", result["reason"])
        self.assertEqual(result["source"], "同花顺 + 开盘啦")

    def test_old_payload_is_never_used_for_new_ladder_date(self):
        payload = {"date": "20261113", "sources": {"kpl": {"records": {
            "002148": {"code": "002148", "date": "20261113", "theme": "旧题材", "reason": "旧原因"}
        }}}}
        self.assertIsNone(attribution_for("002148", "20261116", payload))

    def test_build_payload_retains_only_same_date_evidence(self):
        ladder = {"date": "20261116", "stocks": [{"code": "002148"}, {"code": "002205"}]}
        previous = {"date": "20261116", "sources": {"kpl": {"records": {
            "002148": {"code": "002148", "date": "20261116", "theme": "旧同日题材", "reason": "旧同日原因"},
            "002205": {"code": "002205", "date": "20261113", "theme": "不能沿用", "reason": "不能沿用"},
        }}}}
        payload = build_payload(ladder, {"ths": {"status": "unavailable", "records": {}},
                                      "kpl": {"status": "unavailable", "records": {}}},
                                previous=previous)
        records = payload["sources"]["kpl"]["records"]
        self.assertIn("002148", records)
        self.assertNotIn("002205", records)
        self.assertEqual(payload["coverage"], {"matched": 1, "total": 2})
        self.assertIn("开盘啦不可用（保留1条同日记录）", source_status_text(payload))

        ladder["stocks"] = [{"code": "002205"}]
        self.assertEqual(build_payload(ladder, {}, previous=previous)["coverage"]["matched"], 0)
        ladder["date"] = "20261117"
        self.assertEqual(build_payload(ladder, {}, previous=previous)["coverage"]["matched"], 0)

    def test_conflicting_duplicate_attribution_is_omitted(self):
        rows = [
            {"ts_code": "002148.SZ", "trade_date": "20261116", "lu_desc": "原因A", "theme": "题材A"},
            {"ts_code": "002148.SZ", "trade_date": "20261116", "lu_desc": "原因B", "theme": "题材B"},
        ]
        self.assertEqual(parse_attributions(rows, "kpl", "20261116", ["002148"])["records"], {})
        previous = build_payload(
            {"date": "20261116", "stocks": [{"code": "002148"}]},
            {"kpl": parse_attributions(rows[:1], "kpl", "20261116", ["002148"])},
        )
        conflicting = parse_attributions(rows + rows[:1], "kpl", "20261116", ["002148"])
        result = build_payload({"date": "20261116", "stocks": [{"code": "002148"}]},
                               {"kpl": conflicting}, previous=previous)
        self.assertIsNone(attribution_for("002148", "20261116", result))

    def test_successful_source_supersedes_previous_snapshot(self):
        ladder = {"date": "20261116", "stocks": [{"code": "002148"}, {"code": "002205"}]}
        previous = {"date": "20261116", "sources": {"kpl": {"records": {
            code: {"code": code, "date": "20261116", "theme": "旧题材", "reason": "旧原因"}
            for code in ("002148", "002205")
        }}}}
        response = parse_attributions([
            {"ts_code": "002148.SZ", "trade_date": "20261116", "theme": "新题材"}
        ], "kpl", "20261116", ["002148", "002205"])
        payload = build_payload(ladder, {"kpl": response}, previous=previous)
        self.assertEqual(payload["sources"]["kpl"]["retained_count"], 0)
        self.assertEqual(attribution_for("002148", "20261116", payload)["theme"], "新题材")
        self.assertIsNone(attribution_for("002205", "20261116", payload))

    def test_fetch_anchors_both_sources_and_isolates_permission_failure(self):
        pro = Mock()
        pro.limit_list_ths.side_effect = RuntimeError("接口权限不足 secret-must-not-escape")
        pro.kpl_list.return_value = pd.DataFrame([
            {"ts_code": "002148.SZ", "trade_date": "20261116", "theme": "当日题材"},
            {"ts_code": "002205.SZ", "trade_date": "20261113", "theme": "旧题材"},
        ])
        with patch("daily_ladder_themes.fetch_public_ths", return_value={"status": "unavailable", "records": {}}):
            result = fetch_sources(pro, {"date": "2026-11-16", "stocks": [{"code": "002148"}, {"code": "002205"}]})
        self.assertEqual(pro.limit_list_ths.call_args.kwargs["trade_date"], "20261116")
        self.assertEqual(pro.kpl_list.call_args.kwargs["trade_date"], "20261116")
        self.assertEqual(pro.kpl_list.call_args.kwargs["tag"], "涨停")
        self.assertEqual(result["ths"]["status"], "permission_denied")
        self.assertEqual(set(result["kpl"]["records"]), {"002148"})
        self.assertNotIn("secret-must-not-escape", str(result))

    def test_dashboard_shows_real_theme_and_rejects_old_date(self):
        ladder = {"date": "20261116", "stocks": [{"code": "002148", "name": "测试股票",
                  "industry": "通信服务", "limit_times": 4, "reason": "封板资金 0.26 亿"}]}
        themes = build_payload(ladder, {"kpl": parse_attributions([
            {"ts_code": "002148.SZ", "trade_date": "20261116", "theme": "卫星互联网",
             "lu_desc": "来源提供的当日原因"}
        ], "kpl", "20261116", ["002148"])})
        app = AppTest.from_function(ladder_dashboard, args=(ladder, themes)).run(timeout=30)
        self.assertFalse(app.exception)
        self.assertIn("**题材：卫星互联网**", [m.value for m in app.markdown])
        captions = "\n".join(m.value for m in app.caption)
        for text in ("涨停原因：来源提供的当日原因", "开盘啦 · 20261116", "主营行业：通信服务", "覆盖 1/1"):
            self.assertIn(text, captions)
        themes["date"] = "20261113"
        app = AppTest.from_function(ladder_dashboard, args=(ladder, themes)).run(timeout=30)
        self.assertFalse(app.exception)
        rendered = "\n".join(m.value for m in [*app.markdown, *app.caption])
        self.assertNotIn("卫星互联网", rendered)
        self.assertNotIn("来源提供的当日原因", rendered)
        self.assertIn("当日题材归因暂缺", rendered)

    def test_public_pool_checks_source_date_timestamp_and_completeness(self):
        stamp = int(dt.datetime(2026, 11, 16, 9, 30, tzinfo=CST).timestamp())
        def response(date="20261116", timestamp=stamp, total=1):
            return io.BytesIO(json.dumps({"status_code": 0, "data": {
                "date": date, "page": {"page": 1, "count": 1, "total": total},
                "info": [{"code": "002148", "first_limit_up_time": timestamp,
                          "reason_type": "四足机器人+物联网+手机游戏"}],
            }}).encode())
        with patch("daily_ladder_themes.urllib.request.urlopen", return_value=response()) as request:
            result = fetch_public_ths("20261116", ["002148"])
        self.assertIn("date=20261116", request.call_args.args[0].full_url)
        self.assertEqual(result["records"]["002148"]["theme"], "四足机器人+物联网+手机游戏")
        payload = build_payload({"date": "20261116", "stocks": [{"code": "002148"}]}, {"ths_public": result})
        self.assertEqual(attribution_for("002148", "20261116", payload)["source"], "同花顺涨停揭秘")
        for body, expected in ((response(date="20261113"), "date_mismatch"),
                               (response(timestamp=stamp - 86400), "date_mismatch"),
                               (response(total=2), "incomplete")):
            with patch("daily_ladder_themes.urllib.request.urlopen", return_value=body):
                result = fetch_public_ths("20261116", ["002148"])
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["records"], {})

    def test_public_pool_reads_all_pages_and_does_not_require_token(self):
        responses = [io.BytesIO(json.dumps({"status_code": 0, "data": {
            "date": "20261116", "page": {"page": page, "count": 2, "total": 2},
            "info": [{"code": code, "first_limit_up_time": int(dt.datetime(2026, 11, 16, 10, tzinfo=CST).timestamp()),
                      "reason_type": "来源题材"}],
        }}).encode()) for page, code in ((1, "002148"), (2, "002205"))]
        with patch("daily_ladder_themes.urllib.request.urlopen", side_effect=responses) as request:
            result = fetch_public_ths("20261116", ["002148", "002205"])
        self.assertEqual(set(result["records"]), {"002148", "002205"})
        self.assertEqual(request.call_count, 2)
        with patch("daily_ladder_themes.fetch_public_ths", return_value=result):
            sources = fetch_sources(None, {"date": "20261116", "stocks": [{"code": "002148"}, {"code": "002205"}]})
        self.assertEqual(sources["ths"]["status"], "not_needed")
        self.assertEqual(len(sources["ths_public"]["records"]), 2)


if __name__ == "__main__":
    unittest.main()

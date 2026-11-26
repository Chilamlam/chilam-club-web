import copy
import datetime as dt
import json
import unittest
from unittest.mock import Mock, patch

import decision_workbench as journal
import us_options as calc
import us_options_data as data


def chain(expiry="2030-01-18"):
    options = []
    # Deliberately hypothetical 100 USD underlying, never loaded by the product.
    for strike, cb, ca, pb, pa in ((90, 11, 12, .8, 1), (95, 7, 8, 1.8, 2),
                                  (100, 4.8, 5, 3.8, 4), (105, 1.8, 2, 7, 8), (110, .8, 1, 11, 12)):
        for kind, bid, ask in (("C", cb, ca), ("P", pb, pa)):
            options.append(dict(id=calc.contract_id("SPY", expiry, kind, strike), expiry=expiry,
                kind=kind, strike=strike, bid=bid, ask=ask, multiplier=100, volume=10, open_interest=100))
    return dict(symbol="SPY", spot=100, options=options, source="hypothetical test fixture",
                underlying_asof="2026-11-25", retrieved_at="2026-11-26T06:00:00+00:00",
                quote_time=None, quote_mode="public_reference")


def record(**kwargs):
    option = calc.make_strategy(chain(), "bull_call", "2030-01-18", 100, 105, fee=1)
    fields = dict(symbol="SPY.US", style="us_options", mode="模拟观察", asof="20261126", currency="USD",
        thesis="事件依据", trigger="核对净价", invalid="事件证伪", exit_rule="到期前复核", cost_risk="预算500美元",
        market_evidence={"us_options": option}, **kwargs)
    return journal.new_plan(fields, now="2026-11-26T16:00:00+08:00")


class PayoffTests(unittest.TestCase):
    def test_bull_spread_bid_ask_fees_and_size(self):
        p = calc.make_strategy(chain(), "bull_call", "2030-01-18", 100, 105, contracts=2, fee=1)
        self.assertEqual(p["premium"], 640)
        self.assertEqual(p["fees"], 4)
        m = calc.metrics(p)
        self.assertEqual(m["max_loss"], 644)
        self.assertEqual(m["max_profit"], 356)
        self.assertEqual(m["breakevens"], [103.22])
        self.assertEqual(calc.expiry_pnl(p, 110), 356)
        self.assertEqual(calc.expiry_pnl(p, 99), -644)

    def test_bear_spread_and_long_put_terminal_floor(self):
        p = calc.make_strategy(chain(), "bear_put", "2030-01-18", 100, 95, fee=0)
        self.assertAlmostEqual(calc.metrics(p)["max_loss"], 220)
        self.assertEqual(calc.metrics(p)["breakevens"], [97.8])
        self.assertEqual(calc.expiry_pnl(p, 90), 280)
        long = calc.make_strategy(chain(), "long_put", "2030-01-18", 100, fee=0)
        self.assertEqual(calc.metrics(long)["max_profit"], 9600)
        self.assertEqual(calc.metrics(long)["max_loss"], 400)

    def test_long_call_unlimited_upside_not_infinite_number(self):
        p = calc.make_strategy(chain(), "long_call", "2030-01-18", 100, fee=1)
        self.assertEqual(calc.metrics(p)["max_loss"], 501)
        self.assertIsNone(calc.metrics(p)["max_profit"])
        self.assertEqual(calc.metrics(p)["breakevens"], [105.01])
        json.dumps(p, allow_nan=False)

    def test_protective_put_forward_loss_and_residual_shares(self):
        p = calc.make_strategy(chain(), "protective_put", "2030-01-18", 95, shares=100, fee=0)
        self.assertEqual(calc.metrics(p)["max_loss"], 700)
        self.assertEqual(calc.expiry_pnl(p, 80), -700)
        partial = calc.make_strategy(chain(), "protective_put", "2030-01-18", 95, shares=150, fee=0)
        self.assertEqual(calc.metrics(partial)["max_loss"], 5700)
        self.assertEqual(calc.metrics(partial)["residual_shares"], 50)
        self.assertEqual(calc.expiry_pnl(partial, 80), -1700)

    def test_collar_credit_possible_and_not_free_risk(self):
        p = calc.make_strategy(chain(), "collar", "2030-01-18", 95, 105, shares=100, fee=0)
        self.assertAlmostEqual(calc.metrics(p)["max_loss"], 520)
        self.assertAlmostEqual(calc.metrics(p)["max_profit"], 480)
        self.assertEqual(calc.metrics(p)["breakevens"], [100.2])
        s = chain()
        next(o for o in s["options"] if o["strike"] == 105 and o["kind"] == "C").update(bid=2.5, ask=2.6)
        credit = calc.make_strategy(s, "collar", "2030-01-18", 95, 105, shares=100, fee=0)
        self.assertEqual(credit["premium"], -50)
        self.assertEqual(calc.metrics(credit)["max_loss"], 450)
        partial = calc.make_strategy(s, "collar", "2030-01-18", 95, 105, shares=150, fee=0)
        self.assertIsNone(calc.metrics(partial)["max_profit"])

    def test_quote_hygiene_and_uncovered_or_invalid_structures(self):
        for bad in (None, 0, float("nan"), 9):
            s = chain()
            next(o for o in s["options"] if o["strike"] == 100 and o["kind"] == "C")["bid"] = bad
            with self.assertRaises(ValueError):
                calc.make_strategy(s, "long_call", "2030-01-18", 100)
        for kwargs in (dict(shares=99), dict(shares=150, contracts=2), dict(shares=100, contracts=1.5)):
            with self.assertRaises(ValueError):
                calc.make_strategy(chain(), "collar", "2030-01-18", 95, 105, **kwargs)
        with self.assertRaises(ValueError):
            calc.make_strategy(chain(), "bull_call", "2030-01-18", 105, 100)
        with self.assertRaises(ValueError):
            calc.make_strategy(chain(), "bear_put", "2030-01-18", 95, 100)

    def test_mark_uses_opposite_quote_and_requires_every_original_leg(self):
        s = chain()
        p = calc.make_strategy(s, "bull_call", "2030-01-18", 100, 105, fee=1)
        self.assertAlmostEqual(calc.liquidation_pnl(p, s), -42)
        original = copy.deepcopy(p)
        s["options"] = [o for o in s["options"] if o["id"] != p["legs"][1]["id"]]
        self.assertIsNone(calc.liquidation_pnl(p, s))
        self.assertEqual(p, original)


class DataTests(unittest.TestCase):
    def payload(self):
        return {"data": {"lastTrade": "LAST TRADE: $100.00 (AS OF NOV 25, 2026)", "totalRecord": 2,
            "table": {"asOf": None, "rows": [{"expirygroup": "January 18, 2030"},
                {"strike": "100.00", "drillDownURL": "/market-activity/etf/spy/option-chain/call-put-options/spy---300118c00100000",
                 "c_Bid": "4.80", "c_Ask": "5.00", "p_Bid": "--", "p_Ask": ".10", "c_Openinterest": "1,234"}]}}}

    def test_dates_missing_quotes_and_standard_root(self):
        stamp = "2026-11-26T06:00:00+00:00"
        s = data.parse_nasdaq(self.payload(), "SPY", retrieved_at=stamp)
        self.assertIsNone(s["quote_time"])
        self.assertIsNone(s["options"][1]["bid"])
        self.assertEqual(s["options"][0]["open_interest"], 1234)
        self.assertEqual(s["options"][0]["expiry"], "2030-01-18")
        p = self.payload()
        p["data"]["table"]["rows"][1]["drillDownURL"] = p["data"]["table"]["rows"][1]["drillDownURL"].replace("spy---", "spy1---")
        with self.assertRaises(ValueError):
            data.parse_nasdaq(p, "SPY", retrieved_at=stamp)
        with self.assertRaises(ValueError):
            data.parse_nasdaq(self.payload(), "SPY", retrieved_at="2026-11-24T23:00:00+00:00")

    def test_fetch_checks_pagination_instead_of_silently_truncating(self):
        first = self.payload()
        first["data"]["totalRecord"] = 3
        second = copy.deepcopy(first)
        second["data"]["table"]["rows"] = [dict(first["data"]["table"]["rows"][1], strike="105",
            drillDownURL="/market-activity/etf/spy/option-chain/call-put-options/spy---300118c00105000")]
        replies = [Mock(json=Mock(return_value=p), raise_for_status=Mock()) for p in (first, second)]
        with patch.object(data.requests, "get", side_effect=replies) as get:
            s = data.fetch_chain("SPY", "2030-01-01", "2030-01-31")
            self.assertEqual(len(s["options"]), 4)
            self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 2)


class JournalTests(unittest.TestCase):
    def test_roundtrip_retains_option_contracts_and_currency(self):
        r = record()
        decoded = journal.import_journal(journal.export_journal([r]))[0]
        self.assertEqual(decoded["plan"], r["plan"])
        self.assertIn("SPY300118C00100000", journal.plan_markdown(r))
        self.assertEqual(journal.symbol_id("qqq.us"), "QQQ.US")
        self.assertEqual(journal.symbol_id("QQQ"), "")

    def test_tampered_option_arithmetic_rejected(self):
        r = record()
        r["plan"]["market_evidence"]["us_options"]["premium"] = 1
        with self.assertRaises(ValueError):
            journal.import_journal(journal.export_journal([r]))
        r = record()
        r["plan"]["market_evidence"]["us_options"]["legs"][0]["multiplier"] = 10
        with self.assertRaises(ValueError):
            journal.import_journal(journal.export_journal([r]))

    def test_different_currencies_are_never_summed(self):
        usd = journal.with_outcome(record(), dict(status="已结束", gross_pnl=500, costs=10, closed_on="20261126"))
        cny = copy.deepcopy(usd)
        cny["plan"].update(currency="CNY", style="trend", symbol="600519.SH")
        stats = journal.journal_stats([usd, cny], mode="模拟观察")
        self.assertIsNone(stats["net_total"])
        self.assertIsNone(stats["payoff"])
        self.assertEqual(stats["currency"], "混合币种")
        self.assertEqual(journal.journal_stats([usd, cny], mode="模拟观察", style="us_options")["net_total"], 490)


def render_probe(snapshot):
    import streamlit as st
    from unittest.mock import patch
    import page_us_options as page
    import research_store
    import research_ui
    import journal_store
    with patch.object(journal_store.auth, "is_logged_in", return_value=False):
        if st.session_state.get("show_saved"):
            research_ui.detail(next(iter(research_store.load().values())))
        else:
            with patch.object(page, "_read_snapshot", return_value=snapshot):
                page.render_us_options()


class OptionsUITests(unittest.TestCase):
    def test_source_failure_keeps_previous_timestamp_and_manual_form_available(self):
        from streamlit.testing.v1 import AppTest
        import page_us_options as page
        s = chain((dt.datetime.now(data.NY).date()+dt.timedelta(days=30)).isoformat())
        source = "from page_us_options import render_us_options\nrender_us_options()"
        with patch.object(page, "_fetch", return_value=(s, "")):
            app = AppTest.from_string(source).run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
        with patch.object(page, "_fetch", return_value=(None, "来源暂不可用")):
            app.run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            self.assertTrue(any("上一次成功" in c.value for c in app.caption))
            self.assertTrue(any("来源暂不可用" in c.value for c in app.warning))
            self.assertTrue(app.metric)
            app.radio(key="us_options_source").set_value("录入券商报价").run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            next(b for b in app.button if b.label == "使用这批报价比较").click().run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
            self.assertTrue(app.error)

    def test_three_objectives_guest_save_and_usd_journal(self):
        from streamlit.testing.v1 import AppTest
        expiry = (dt.datetime.now(data.NY).date()+dt.timedelta(days=30)).isoformat()
        app = AppTest.from_function(render_probe, args=(chain(expiry),)).run(timeout=30)
        self.assertFalse(app.exception, str(app.exception))
        for goal in ("看跌", "保护已有持仓", "看涨"):
            app.radio(key="us_options_goal").set_value(goal).run(timeout=30)
            self.assertFalse(app.exception, str(app.exception))
        next(w for w in app.text_area if w.label == "交易或保护的依据").input("测试预案：有日期的事件")
        for label in ("什么条件满足才行动", "什么变化会推翻判断", "止盈、止损或提前退出安排"):
            next(w for w in app.text_input if w.label == label).input("测试个人条件")
        next(b for b in app.button if b.label == "保存期权预案").click().run(timeout=30)
        self.assertFalse(app.exception, str(app.exception))
        records = app.session_state["decision_journal_guest"]
        self.assertEqual(len(records), 1)
        frozen = copy.deepcopy(next(iter(records.values()))["plan"])
        next(b for b in app.button if b.label == "保存期权预案").click().run(timeout=30)
        self.assertEqual(len(app.session_state["decision_journal_guest"]), 1)
        app.session_state["show_saved"] = True
        app.run(timeout=30)
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any("USD" in w.label for w in app.number_input))
        self.assertFalse(any(b.label == "查看技术图 →" for b in app.button))
        next(w for w in app.selectbox if w.label == "跟踪状态").select("已结束")
        next(w for w in app.number_input if w.label.startswith("毛盈亏")).set_value(100.0)
        next(w for w in app.number_input if w.label.startswith("总成本")).set_value(10.0)
        next(w for w in app.date_input if w.label == "结束日期").set_value(dt.datetime.now().date())
        next(b for b in app.button if b.label == "保存跟踪结果").click().run(timeout=30)
        self.assertFalse(app.exception, str(app.exception))
        result = next(iter(app.session_state["decision_journal_guest"].values()))
        self.assertEqual(result["plan"], frozen)
        self.assertEqual(result["outcome"]["net_pnl"], 90)


if __name__ == "__main__":
    unittest.main()

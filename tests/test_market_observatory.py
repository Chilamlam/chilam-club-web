"""Financial semantics and public page regressions; no network or private accounts."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import market_watch as calc
import market_watch_data as data
import global_markets as world
from update_schedule import CST


def calendar():
    days = pd.bdate_range("2026-10-01", "2027-01-29").strftime("%Y%m%d").tolist()
    return dict(start_date=days[0], end_date=days[-1], open_dates=days)


def etfs():
    days = calendar()["open_dates"][:25]
    return [dict(date=day, symbol=code, name=name, index=calc.ETF_POOL[code], close=3.,
                 amount_yi=20. if i == 24 else 10., change_pct=1., source="test")
            for code,name in (("510300.SH","沪深300ETF"),("159915.SZ","创业板ETF"))
            for i,day in enumerate(days)]


def event():
    return dict(id="1", title="Fed Decision in December?", slug="fed-decision", active=True,
        closed=False, endDate="2026-12-10T04:59:00Z", volume24hr=100,
        markets=[dict(id="2", active=True, closed=False, outcomes='["Yes","No"]',
            outcomePrices='["0.7","0.3"]', clobTokenIds='["123","456"]', groupItemTitle="25 bps decrease",
            question="Will the Fed cut?", oneDayPriceChange=.02, bestBid=.69, bestAsk=.71)])


class CalculationTests(unittest.TestCase):
    def test_financing_uses_previous_session_not_previous_available_row(self):
        frame=pd.DataFrame([dict(date=d,balance_yi=v) for d,v in
            (("20261119",100),("20261120",110),("20261124",120),("20261125",125))])
        result=calc.financing_history(frame,calendar()).set_index("date")
        self.assertEqual(result.loc["20261120","change_yi"],10)
        self.assertTrue(pd.isna(result.loc["20261124","change_yi"]))
        self.assertEqual(result.loc["20261125","change_yi"],5)

    def test_conflicting_dates_are_not_arbitrarily_selected(self):
        frame=pd.DataFrame([dict(date="20261123",up=2,down=3),dict(date="20261123",up=3,down=3)])
        self.assertTrue(calc.breadth_history(frame,pd.DataFrame()).empty)

    def test_breadth_does_not_relabel_threshold_limit_counts(self):
        frame=pd.DataFrame([dict(date="20261123",up=2,down=3,limit_up=100,limit_down=50)])
        result=calc.breadth_history(frame,pd.DataFrame([dict(date="20261120",total_amount_yi=100)]))
        self.assertNotIn("limit_up",result)
        self.assertEqual(result.iloc[0].up_ratio,40)
        self.assertTrue(pd.isna(result.iloc[0].total_amount_yi))

    def test_basis_matches_date_direction_and_expiry(self):
        futures=[dict(date="20261123",contract="IF2612",close=3990),dict(date="20261123",contract="IH2612",close=3100)]
        indexes=[dict(date="20261123",prefix="IF",close=4000),dict(date="20261120",prefix="IH",close=3000)]
        rows=calc.basis_rows(futures,indexes,calendar())
        self.assertEqual(len(rows),1)
        r=rows[0]
        self.assertEqual((r["discount"],r["days"]),(10,25))
        self.assertAlmostEqual(r["annual_pct"],.25*365/25)
        net, annual=calc.net_basis(r,12)
        self.assertEqual(net,-2)
        self.assertLess(annual,0)
        self.assertEqual(calc.net_basis(r,None),(None,None))
        indexes.append(dict(date="20261123",prefix="IF",close=4100))
        self.assertFalse(calc.basis_rows(futures,indexes,calendar()))

    def test_expiry_zero_days_and_holiday_roll(self):
        rows=calc.basis_rows([dict(date="20261218",contract="IF2612",close=4010)],
                            [dict(date="20261218",prefix="IF",close=4000)],calendar())
        self.assertLess(rows[0]["discount"],0)
        self.assertIsNone(rows[0]["annual_pct"])
        cal=calendar();cal["open_dates"].remove("20261218")
        self.assertEqual(calc.expiry("IF2612",cal)[0],dt.date(2026,12,21))

    def test_etf_prior_20_excludes_current_and_requires_all_dates(self):
        frame=pd.DataFrame(etfs())
        result=calc.etf_activity(frame,calendar())
        last=result[result.symbol=="510300.SH"].iloc[-1]
        self.assertEqual((last.mean20_yi,last.amount_ratio),(10,2))
        frame=frame.drop(frame[(frame.symbol=="510300.SH")&(frame.date==calendar()["open_dates"][12])].index)
        result=calc.etf_activity(frame,calendar())
        self.assertTrue(pd.isna(result[result.symbol=="510300.SH"].iloc[-1].amount_ratio))
        self.assertEqual(result[result.symbol=="159915.SZ"].iloc[-1].amount_ratio,2)
        pool=calc.etf_pool_history(result,["510300.SH","159915.SZ","588000.SH"])
        self.assertTrue(pool.amount_yi.isna().all())
        self.assertTrue(calc.etf_pool_history(result,["588000.SH"]).empty)

    def test_invalid_amount_does_not_become_volume_signal(self):
        rows=etfs();rows[24]["amount_yi"]=-10
        last=calc.etf_activity(pd.DataFrame(rows),calendar()).query('symbol == "510300.SH"').iloc[-1]
        self.assertTrue(pd.isna(last.amount_ratio))

    def test_publish_retains_history_on_partial_and_failure(self):
        with tempfile.TemporaryDirectory() as root, patch.object(data,"OUT",Path(root)):
            a=data.bundle([dict(date="20261120",symbol="a",amount_yi=10)],"test")
            data.publish("etf",a)
            data.publish("etf",data.bundle([dict(date="20261123",symbol="b",amount_yi=20)],"test",["a unavailable"]))
            self.assertEqual(len(data.read_archive("etf")["rows"]),2)
            data.publish("etf",data.bundle([],"test",["outage"]))
            saved=data.read_archive("etf")
            self.assertEqual(len(saved["rows"]),2)
            self.assertEqual(saved["last_attempt"]["status"],"unavailable")


class GlobalMarketTests(unittest.TestCase):
    def test_yes_price_and_percentage_point_change(self):
        e=event();r=world.normalize_events([e])[0]["markets"][0]
        self.assertEqual((r["probability"],r["change_pp"]),(70,2))
        self.assertAlmostEqual(r["spread_pp"],2)
        e["markets"][0].update(outcomes='["No","Yes"]',oneDayPriceChange=None)
        r=world.normalize_events([e])[0]["markets"][0]
        self.assertEqual((r["probability"],r["token"]),(30,"456"))
        self.assertIsNone(r["change_pp"])
        self.assertIsNone(r["spread_pp"])

    def test_closed_nonbinary_and_invalid_quotes_are_not_candidates(self):
        for field,value in (("closed",True),("outcomePrices",'["NaN","0.3"]'),
                            ("outcomes",'["Yes","No","Maybe"]')):
            e=event();e["markets"][0][field]=value
            self.assertFalse(world.normalize_events([e]))

    def test_chain_asset_alignment_and_units(self):
        payload=[{"universe":[{"name":"BTC"}]},[{"markPx":"100","prevDayPx":"80","openInterest":"3","oraclePx":"99"}]]
        r=world.normalize_chain(payload,"2026-11-23T20:00:00+08:00")[0]
        self.assertEqual((r["price"],r["change24"],r["oi_usd"]),(100,25,300))
        self.assertIsNone(r["volume24"])
        with self.assertRaises(ValueError): world.normalize_chain([payload[0],[]],"")

    def test_candles_anchor_at_close_excludes_incomplete_and_empty(self):
        close=2_000_000
        candles=[dict(t=close-900000,T=close-1,c=10,v=5),
                 dict(t=close,T=close+899999,c=11,v=2),
                 dict(t=close+900000,T=close+1799999,c=12,v=3),
                 dict(t=close+1800000,T=close+2699999,c=13,v=0)]
        r=world.candle_observation(candles,close,close+1000000)
        self.assertEqual(r["baseline"]["close"],10)
        self.assertEqual((len(r["candles"]),r["volume"]),(1,2))
        r=world.candle_observation(candles[1:],close,close+3000000)
        self.assertIsNone(r["baseline"])
        self.assertEqual(r["volume"],5)

    def test_intraday_and_weekend_use_latest_completed_ashare_close(self):
        with patch.object(world,"calendar",return_value=calendar()):
            for now,expected in ((dt.datetime(2026,11,23,14,59,tzinfo=CST),"2026-11-20"),
                                 (dt.datetime(2026,11,23,15,0,tzinfo=CST),"2026-11-23"),
                                 (dt.datetime(2026,11,22,23,0,tzinfo=CST),"2026-11-20")):
                self.assertEqual(world.ashare_close(now).date().isoformat(),expected)

    def test_old_quote_is_not_compared_to_new_close_anchor(self):
        h=dict(baseline=dict(close=10),close_at="2026-11-23T15:00:00+08:00")
        self.assertIsNone(world.after_close_change(dict(price=11,checked_at="2026-11-20T21:00:00+08:00"),h))
        self.assertAlmostEqual(world.after_close_change(dict(price=11,checked_at="2026-11-23T21:00:00+08:00"),h),10)


class PageTests(unittest.TestCase):
    def test_etf_search_pagination_and_missing_index(self):
        from streamlit.testing.v1 import AppTest
        import page_market_observatory as page
        snap=data.bundle(etfs(),"test",cutoff=calendar()["open_dates"][24])
        with patch.object(data,"read_archive",return_value=snap),patch.object(data,"calendar",return_value=calendar()):
            app=AppTest.from_string('from page_market_observatory import render_etf\nrender_etf()',default_timeout=20).run()
            self.assertFalse(app.exception)
            app.text_input(key="etf_activity_query").input("CYB").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[0].value["代码"].tolist(),["159915.SZ"])
            app.button(key="etf_activity_chart").click().run()
            self.assertEqual(app.session_state["journey_target"]["symbol"],"sz159915")
            app.multiselect(key="etf_activity_indices").set_value(["科创50"]).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("没有日线" in i.value for i in app.info))

    def test_futures_historical_selection_is_not_outage(self):
        from streamlit.testing.v1 import AppTest
        rows=calc.basis_rows([dict(date=d,contract="IF2612",close=3990) for d in ("20261120","20261123")],
            [dict(date=d,prefix="IF",close=4000) for d in ("20261120","20261123")],calendar())
        with patch.object(data,"read_archive",return_value=data.bundle(rows,"test",cutoff="20261123")), \
             patch.object(data,"last_close_day",return_value="20261123"):
            app=AppTest.from_string('from page_market_observatory import render_futures\nrender_futures()').run()
            app.selectbox(key="basis_date").select("20261120").run()
            self.assertFalse(app.exception)
            self.assertFalse(app.warning)
            with patch.object(data,"last_close_day",return_value="20261124"):
                app.run()
                self.assertTrue(app.warning)
            app.selectbox(key="basis_detail").select("IF").run()
            app.number_input(key="basis_div_points").set_value(12.).run()
            self.assertFalse(app.exception)

    def test_global_pages_work_when_history_is_unavailable(self):
        from streamlit.testing.v1 import AppTest
        import page_market_observatory as page
        for name,rows in (("polymarket",world.normalize_events([event()])),
            ("onchain",world.normalize_chain([{"universe":[{"name":"BTC"}]},[{"markPx":100}]],data.beijing_now().isoformat()))):
            snap=data.bundle(rows,"test")
            with patch.object(page,"fetch_snapshot",return_value=snap),patch.object(page,"fetch_history",return_value=(None,"历史未取得")):
                app=AppTest.from_string(f'from page_market_observatory import render_{name}\nrender_{name}()').run()
                self.assertFalse(app.exception)
                self.assertTrue(len(app.dataframe)>0)


if __name__ == "__main__":
    unittest.main()

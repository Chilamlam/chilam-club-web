import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

import candidate_compare as compare
import decision_workbench as engine
import journal_store
import research_store as store
import research_workspace as workspace
from tests.test_decision_workbench import plan


def candidate(code="000001.SZ", date="20261117"):
    return dict(symbol=code, name="样本", date=date, industry="银行", reasons=["可核验的归档依据"],
                sources=["data/strong_stocks.csv"], risks=[], rps=95)


def conditions(record, **changes):
    result = workspace.research(record)
    result.update(changes)
    return result


class RevisionAndRiskTests(unittest.TestCase):
    def test_revision_keeps_original_and_execution_update_keeps_research(self):
        original = plan()
        before = copy.deepcopy(original)
        revised = workspace.revise(original, conditions(original, trigger="等待公告兑现", review_on="20261120"),
                                   now="2026-11-18T20:00:00+08:00", reason="新公告改变原判断")
        self.assertEqual(original, before)
        self.assertEqual(revised["plan"], before["plan"])
        self.assertEqual(workspace.research(revised)["trigger"], "等待公告兑现")
        closed = engine.with_outcome(revised, dict(status="已结束", gross_pnl=100, costs=8, closed_on="20261120"))
        self.assertEqual(closed["outcome"]["research_history"], revised["outcome"]["research_history"])
        self.assertEqual(closed["outcome"]["net_pnl"], 92)
        self.assertEqual(engine.import_journal(engine.export_journal([closed]))[0]["outcome"]["research"], revised["outcome"]["research"])

    def test_prices_never_use_adjusted_stale_or_future_quotes(self):
        record = plan()
        record = workspace.revise(record, conditions(record, invalid_below=10, price_basis_date="20261118"), now="2026-11-18T20:00:00+08:00")
        for quote in ({"date":"20261118","close":8,"adjusted":True},
                      {"date":"20261117","close":8,"adjusted":False},
                      {"date":"20261120","close":8,"adjusted":False}, {}):
            checks = workspace.price_checks(record, quote, today="20261118")
            self.assertFalse(any(c["kind"] == "price" for c in checks))
            self.assertTrue(any(c["kind"] == "missing" for c in checks))
        checks = workspace.price_checks(record, {"date":"20261118","close":10,"adjusted":False}, today="20261118")
        self.assertTrue(any(c["kind"] == "price" for c in checks))

    def test_due_dates_independent_of_quote_and_closed_not_alerted(self):
        record = workspace.revise(plan(), conditions(plan(), review_on="20261118"), now="2026-11-18T20:00:00+08:00")
        self.assertEqual(workspace.price_checks(record, None, today="20261118")[0]["kind"], "due")
        record["outcome"]["status"] = "已放弃"
        self.assertEqual(workspace.price_checks(record, None, today="20261118"), [])

    def test_invalid_inputs_and_pretend_real_position_are_rejected(self):
        record = plan()
        for changes in ({"observe_low":12,"observe_high":10}, {"invalid_below":0},
                        {"observe_low":10}, {"cost_price":float("nan")},
                        {"shares":100,"cost_price":10,"position_date":"20261118"}):
            with self.assertRaises(ValueError):
                workspace.revise(record, conditions(record, **changes), now="2026-11-18T20:00:00+08:00")

    def test_import_rejects_malformed_research_and_keeps_original(self):
        record = plan()
        record["outcome"]["research"] = {"shares": -10}
        with self.assertRaises(ValueError):
            engine.import_journal(engine.export_journal([record]))

    def test_backup_normalizes_numeric_price_strings_before_comparison(self):
        record=plan()
        record["outcome"]["research"]={"invalid_below":"10.0","price_basis_date":"2026-11-17"}
        restored=engine.import_journal(engine.export_journal([record]))[0]
        checks=workspace.price_checks(restored,dict(close=9,date="20261118",adjusted=False),today="20261118")
        self.assertTrue(any(c["kind"]=="price" for c in checks))

    def test_portfolio_uses_cost_quantity_and_same_date_coverage(self):
        a = plan(mode="实盘记录", symbol="000001.SZ")
        a = workspace.revise(a, conditions(a, shares=100,cost_price=10,position_date="20261117"), now="2026-11-18T20:00:00+08:00")
        b = plan(mode="实盘记录", symbol="600519.SH")
        b = workspace.revise(b, conditions(b, shares=100,cost_price=20,position_date="20261117"), now="2026-11-18T20:00:00+08:00")
        quotes={"000001.SZ":dict(close=11,date="20261118",adjusted=False),"600519.SH":dict(close=22,date="20261118",adjusted=False)}
        result=workspace.holdings([a,b],quotes)
        self.assertEqual(result["total"],3300)
        self.assertAlmostEqual(result["rows"][0]["weight"],1/3)
        self.assertEqual(result["rows"][0]["pnl"],100)
        quotes["600519.SH"]["date"]="20261117"
        self.assertIsNone(workspace.holdings([a,b],quotes)["total"])
        duplicate=copy.deepcopy(a); duplicate["id"]="another-id"
        self.assertEqual(workspace.holdings([a,duplicate],quotes)["duplicates"],["000001.SZ"])


class CandidateComparisonTests(unittest.TestCase):
    def test_incomplete_history_cannot_claim_new_or_continuing(self):
        document={"date":"20261117","styles":{"trend":{"complete":False,"rows":[]}}}
        self.assertEqual(compare.membership("000001.SZ","trend",{"20261117":document},"20261117"),"历史待积累")
        document["styles"]["trend"]["complete"]=True
        self.assertEqual(compare.membership("000001.SZ","trend",{"20261117":document},"20261117"),"较上一交易日新增")
        document["styles"]["trend"]["rows"]=[candidate()]
        self.assertEqual(compare.membership("000001.SZ","trend",{"20261117":document},"20261117"),"延续入选")

    def test_wrong_date_quotes_never_fill_current_price(self):
        stale=pd.DataFrame([dict(ts_code="000001.SZ",name="旧",close=1,date="20261116")])
        fresh=pd.DataFrame([dict(ts_code="000001.SZ",name="新",price_now=2,更新日期="20261117")])
        self.assertEqual(compare.raw_quotes({"strong":fresh},stale,"20261117")["000001.SZ"]["close"],2)
        self.assertEqual(compare.raw_quotes({},stale,"20261117"),{})

    def test_unknown_values_remain_last_and_not_pass_numeric_filter(self):
        rows=[candidate("000001.SZ"),candidate("000002.SZ"),candidate("000003.SZ")]
        for row,val in zip(rows,(None,-5,10)):
            row["return5"]=val
        self.assertEqual([r["symbol"] for r in compare.select(rows,sort="5日涨跌幅",ascending=True)], ["000002.SZ","000003.SZ","000001.SZ"])
        self.assertEqual([r["symbol"] for r in compare.select(rows,max_return5=0)],["000002.SZ"])


class AutomaticStorageTests(unittest.TestCase):
    def test_guest_copy_requires_login_and_does_not_duplicate_or_move_other_accounts(self):
        source=plan()
        state={"decision_journal_guest":{source["id"]:source},"decision_journal_8":{"foreign":plan()}}
        with patch.object(store.st,"session_state",state), patch.object(journal_store,"_owner",return_value=None):
            self.assertEqual(store.copy_guest_records(),(0,0))
        def written(record):
            saved=copy.deepcopy(record);saved.update(_synced=True,_dirty=False,revision=1)
            return saved,None
        with patch.object(store.st,"session_state",state), patch.object(journal_store,"_owner",return_value=7), \
             patch.object(journal_store,"load_records",return_value=([],None)),patch.object(journal_store,"save_record",side_effect=written):
            self.assertEqual(store.copy_guest_records(),(1,0))
            self.assertEqual(store.copy_guest_records(),(0,0))
            self.assertEqual(len(state["decision_journal_7"]),1)
            self.assertIn(source["id"],state["decision_journal_guest"])
            saved=next(iter(state["decision_journal_7"].values()))
            self.assertNotEqual(saved["id"],source["id"])
            self.assertEqual(saved["plan"]["guest_source_id"],source["id"])

    def test_load_once_and_never_overwrite_unsaved_changes(self):
        state={}
        original=plan()
        with patch.object(store.st,"session_state",state), patch.object(journal_store,"_owner",return_value=7), \
             patch.object(journal_store,"load_records",return_value=([copy.deepcopy(original)],None)) as read:
            records=store.load(); store.load()
            self.assertEqual(read.call_count,1)
            records[original["id"]]["outcome"]["notes"]="未保存的新判断"
            records[original["id"]]["_dirty"]=True
            store.load(force=True)
            self.assertEqual(records[original["id"]]["outcome"]["notes"],"未保存的新判断")

    def test_failed_save_keeps_content_and_account_switch_isolated(self):
        state={}
        identity=[7]
        record=plan()
        with patch.object(store.st,"session_state",state), patch.object(journal_store,"_owner",side_effect=lambda:identity[0]), \
             patch.object(journal_store,"save_record",return_value=(None,{"message":"internal token hidden"})), \
             patch.object(journal_store,"load_records",return_value=([],None)):
            saved,err=store.save(record)
            self.assertTrue(saved["_dirty"])
            self.assertNotIn("internal token",store.error_text(err))
            identity[0]=8
            self.assertEqual(store.load(),{})
            identity[0]=7
            self.assertIn(record["id"],store.load())

    def test_lost_insert_response_recovers_same_account_same_content(self):
        record=plan()
        remote=copy.deepcopy(record);remote.update(user_id=7,revision=1)
        with patch.object(journal_store,"_owner",return_value=7), patch.object(journal_store.database,"_supabase_request",
            side_effect=[(None,{"code":"timeout"}),([remote],None)]) as request:
            saved,err=journal_store.save_record(record)
            self.assertIsNone(err);self.assertTrue(saved["_synced"])
            self.assertEqual(request.call_args.kwargs["params"]["user_id"],"eq.7")
            self.assertNotIn("user_id",saved)

    def test_lost_update_response_and_real_conflict_are_distinct(self):
        record=plan();record.update(_synced=True,revision=3)
        record["outcome"]["notes"]="新条件"
        remote=copy.deepcopy(record);remote["revision"]=4
        with patch.object(journal_store,"_owner",return_value=7), patch.object(journal_store.database,"_supabase_request",
            side_effect=[(None,{"code":"timeout"}),([remote],None)]):
            self.assertIsNone(journal_store.save_record(record)[1])
        remote["outcome"]["notes"]="另一个窗口的条件"
        with patch.object(journal_store,"_owner",return_value=7), patch.object(journal_store.database,"_supabase_request",
            side_effect=[(None,{"code":"timeout"}),([remote],None)]):
            self.assertEqual(journal_store.save_record(record)[1]["code"],"CONFLICT")


class FullArchiveTests(unittest.TestCase):
    def test_truncated_pool_is_explicitly_partial_and_new_pool_is_not_truncated(self):
        from evening_plan import build_pool_10am_first
        ladder={"stocks":[dict(code=f"{i:06d}",name=f"样本{i}",limit_times=1,first_time="09:35:00",reason="炸板0次") for i in range(1,31)]}
        pool=build_pool_10am_first(ladder,{}, {})
        self.assertEqual(pool["count"],30)
        self.assertEqual(len(pool["items"]),30)
        pool["items"]=pool["items"][:20]
        evening=dict(date="20261118",status="complete",pools={"pool_10am_first":pool,"pool_2b_pk":{"count":0,"items":[]}})
        result=compare.archive({},evening,asof="20261118")
        self.assertFalse(result["styles"]["relay"]["complete"])
        self.assertTrue(any("20/30" in text for text in result["styles"]["relay"]["missing"]))

    def test_current_candidates_and_departed_etfs_continue_to_get_prices(self):
        from daily_scorecard import full_candidate_codes
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/"data/candidates/history").mkdir(parents=True)
            pd.DataFrame([dict(ts_code="000001.SZ",更新日期="20261118")]).to_csv(root/"data/strong_stocks.csv",index=False)
            pd.DataFrame([dict(ts_code="159915.SZ",更新日期="20261118")]).to_csv(root/"data/strong_etfs.csv",index=False)
            past={"date":"20261117","styles":{"etf":{"rows":[{"symbol":"510300.SH"}]}}}
            future={"date":"20261119","styles":{"etf":{"rows":[{"symbol":"159999.SZ"}]}}}
            for name,value in (("20261117",past),("20261119",future)):
                (root/f"data/candidates/history/{name}.json").write_text(json.dumps(value),encoding="utf-8")
            previous=os.getcwd()
            try:
                os.chdir(root)
                codes,funds=full_candidate_codes("20261118")
            finally:
                os.chdir(previous)
            self.assertEqual(codes,{"000001.SZ","159915.SZ","510300.SH"})
            self.assertEqual(funds,{"159915.SZ","510300.SH"})

    def test_partial_rerun_never_overwrites_a_complete_style_archive(self):
        from daily_candidate_archive import build
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/"data/candidates/history").mkdir(parents=True)
            pd.DataFrame([dict(ts_code="159915.SZ",name="ETF",price_now=2,RPS_50=90,更新日期="20261118")]).to_csv(root/"data/strong_etfs.csv",index=False)
            first=build(root)
            self.assertTrue(first["styles"]["etf"]["complete"])
            (root/"data/strong_etfs.csv").write_text("ts_code,name,更新日期\n",encoding="utf-8")
            pd.DataFrame([dict(ts_code="000001.SZ",name="银行",RPS_50=90,更新日期="20261118")]).to_csv(root/"data/strong_stocks.csv",index=False)
            second=build(root)
            self.assertEqual(second["styles"]["etf"],first["styles"]["etf"])


def review_app(section):
    from page_decision_workbench import render_decision_workbench_page
    render_decision_workbench_page(section)


class WorkspaceFlowTests(unittest.TestCase):
    def test_manual_fundamental_handoff_keeps_style_on_submit(self):
        import page_decision_workbench as page
        context=({}, {}, "20261117",dict(expected_date="20261117",status="current",message=""),{})
        with patch.object(journal_store,"_owner",return_value=None),patch.object(page,"_context",return_value=context):
            app=AppTest.from_function(review_app,args=("tracking",))
            app.session_state["decision_journal_guest_new_style"]="fundamental"
            app.run(timeout=30)
            next(w for w in app.text_input if w.label=="证券代码（含交易所）").input("600519.SH")
            next(w for w in app.text_input if w.label=="这次为什么关注").input("阅读最新财报")
            next(b for b in app.button if b.label=="建立研究档案").click().run(timeout=30)
            self.assertFalse(app.exception,str(app.exception))
            record=next(iter(app.session_state["decision_journal_guest"].values()))
            self.assertEqual(record["plan"]["style"],"fundamental")

    def test_create_edit_and_close_same_stock_dossier(self):
        import page_decision_workbench as page
        context=({}, {}, "20261117",dict(expected_date="20261117",status="current",message=""),{})
        analysis={"prices":pd.DataFrame(),"calendar":{},"plans":{}}
        with patch.object(journal_store,"_owner",return_value=None),patch.object(page,"_context",return_value=context), \
             patch.object(page.insights,"load_insights",return_value=analysis):
            app=AppTest.from_function(review_app,args=("tracking",)).run(timeout=30)
            next(w for w in app.text_input if w.label=="证券代码（含交易所）").input("000001.SZ")
            next(w for w in app.text_input if w.label=="名称").input("样本")
            next(w for w in app.text_input if w.label=="这次为什么关注").input("订单公告")
            next(b for b in app.button if b.label=="建立研究档案").click().run(timeout=30)
            self.assertFalse(app.exception,str(app.exception))
            records=app.session_state["decision_journal_guest"]
            ident=next(iter(records));frozen=copy.deepcopy(records[ident]["plan"])
            next(w for w in app.text_area if w.label=="下一步要等什么").input("等待下一期订单数据")
            next(b for b in app.button if b.label=="保存研究与条件").click().run(timeout=30)
            self.assertFalse(app.exception,str(app.exception))
            self.assertEqual(workspace.research(app.session_state["decision_journal_guest"][ident])["trigger"],"等待下一期订单数据")
            next(w for w in app.selectbox if w.label=="跟踪状态").select("已放弃")
            next(w for w in app.text_area if w.label.startswith("实际发生")).input("新公告与原判断不符")
            next(b for b in app.button if b.label=="保存跟踪结果").click().run(timeout=30)
            self.assertEqual(app.session_state["decision_journal_guest"][ident]["plan"],frozen)
            self.assertEqual(app.session_state["decision_journal_guest"][ident]["outcome"]["status"],"已放弃")

    def test_home_and_candidate_missing_sources_are_usable(self):
        import page_decision_workbench as page
        context=({}, {}, "20261117",dict(expected_date="20261117",status="current",message=""),{})
        with patch.object(journal_store,"_owner",return_value=None),patch.object(page,"_context",return_value=context):
            for section in ("home","candidates","tracking"):
                app=AppTest.from_function(review_app,args=(section,)).run(timeout=30)
                self.assertFalse(app.exception,str(app.exception))


if __name__ == "__main__":
    unittest.main()

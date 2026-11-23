import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from candidate_compare import select
from stock_search import matches_stock, name_aliases, stock_label, stock_mask


def promotion_page(promo):
    from page_sentiment import _render_promotion_table
    _render_promotion_table(promo)


def picker_page():
    import pandas as pd
    from investor_journey import candidate_actions
    candidate_actions(pd.DataFrame([
        {"ts_code": "603324.SH", "name": "盛剑科技"},
        {"ts_code": "000905.SH", "name": "中证500"},
        {"ts_code": "000905.SZ", "name": "厦门港务"},
    ]), key="search_test")


def large_promotion():
    # Tomorrow's one-to-two cohort can easily contain more than 100 entries.
    first = [{"code": f"{i:06}", "name": "盛剑科技" if i == 136 else f"测试股票{i}",
              "previous_height": 1, "current_height": 2 if i % 2 else None,
              "promoted": bool(i % 2)} for i in range(137)]
    high = [{"code": f"60000{i}", "name": f"高位股票{i}", "previous_height": 4,
             "current_height": 5 if i else None, "promoted": bool(i)} for i in range(2)]
    rates = {}
    for path, members in (("1进2", first), ("4进5", high)):
        promoted = sum(r["promoted"] for r in members)
        rates[path] = dict(base=len(members), promoted=promoted, rate=promoted/len(members), members=members)
    return dict(date="20261124", prev_date="20261123", rates=rates)


class StockSearchTests(unittest.TestCase):
    def test_initials_phrase_readings_and_mixed_names(self):
        for name, initials in (("盛剑科技", "sjkj"), ("真视通", "zst"), ("中京电子", "zjdz"),
                               ("平安银行", "payh"), ("重庆银行", "cqyh"), ("长安汽车", "caqc"),
                               ("厦门银行", "xmyh"), ("*ST中科", "stzk"), ("沪深300ETF", "hs300etf")):
            with self.subTest(name=name):
                self.assertEqual(name_aliases(name)[0], initials)
                self.assertTrue(matches_stock(initials.upper(), name, "000001.SZ"))
                self.assertIn(initials.upper(), stock_label(name, "000001.SZ"))
        for query in ("sjkj", " SJKJ ", "ＳＪＫＪ", "盛剑", "603324", "SH", "shengjiankeji"):
            self.assertTrue(matches_stock(query, "盛剑科技", "603324.SH"))
        self.assertFalse(matches_stock(".*", "盛剑科技", "603324.SH"))
        self.assertFalse(matches_stock("none", None, pd.NA))

    def test_filter_keeps_index_exchange_identity_and_missing_values(self):
        frame = pd.DataFrame({"name": ["厦门港务", "中证500", None],
                              "ts_code": ["000905.SZ", "000905.SH", None]}, index=[17, 9, 28])
        self.assertEqual(frame[stock_mask(frame, "xmgw")]["ts_code"].tolist(), ["000905.SZ"])
        self.assertEqual(stock_mask(frame, "000905").tolist(), [True, True, False])
        rows = [dict(symbol="603324.SH", name="盛剑科技", industry="环保", risks=[], sources=[])]
        for query in ("SJKJ", "环保"):
            self.assertEqual(select(rows, query=query), rows)

    def test_picker_search_labels_preserve_underlying_symbol(self):
        app = AppTest.from_function(picker_page).run()
        picker = app.selectbox[0]
        self.assertIsNone(picker.value)
        self.assertTrue(app.button(key="search_test_chart").disabled)
        self.assertIn("SJKJ", picker.options[0])
        self.assertIn("sh000905", picker.options[1])
        picker.select("sz000905").run()
        app.button(key="search_test_track").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["journey_target"]["symbol"], "sz000905")


class PromotionPagingTests(unittest.TestCase):
    scope = "promotion_20261123_20261124"

    def test_large_cohort_is_collapsed_paged_searchable_and_not_truncated(self):
        app = AppTest.from_function(promotion_page, args=(large_promotion(),)).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.expander[0].proto.expanded)
        self.assertFalse(app.expander[1].proto.expanded)
        self.assertEqual(app.dataframe[0].value["昨日基数"].sum(), 139)
        self.assertEqual(len(app.dataframe[1].value), 2)  # highest yesterday's board first
        app.selectbox(key=self.scope+"_height").select("1进2").run()
        self.assertEqual(len(app.dataframe[1].value), 20)
        codes = []
        for page in range(1, 8):
            app.selectbox(key=self.scope+"_page").select(page).run()
            self.assertLessEqual(len(app.dataframe[1].value), 20)
            codes.extend(app.dataframe[1].value["代码"].tolist())
        self.assertEqual(len(set(codes)), 137)
        app.text_input[0].input("SJKJ").run()
        self.assertEqual(app.dataframe[1].value["代码"].tolist(), ["000136"])
        self.assertEqual(app.dataframe[1].value["今日连板数"].tolist(), ["—"])
        self.assertEqual(app.selectbox(key=self.scope+"_page").value, 1)
        app.radio[0].set_value("晋级").run()
        self.assertEqual(len(app.dataframe), 1)  # summary retained with zero matching details
        self.assertTrue(any("没有股票" in i.value for i in app.info))
        app.text_input[0].input("").run()
        self.assertTrue(all(app.dataframe[1].value["今日结果"] == "晋级"))
        app.selectbox(key=self.scope+"_height").select("全部高度").run()
        self.assertEqual(app.dataframe[1].value.iloc[0]["昨日板数"], "4板")
        self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()

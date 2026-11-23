import ast
import datetime as dt
from pathlib import Path
import unittest

import pandas as pd

import data_freshness as freshness
from stock_search import stock_mask


class FreshnessTests(unittest.TestCase):
    today = dt.date(2026, 11, 16)

    def test_date_formats_and_calendar_validity(self):
        for value in ('20261113', '2026-11-13', '2026-11-13 18:00:00',
                      '2026-11-13T18:00:00+08:00', 20261113, 20261113.0):
            with self.subTest(value=value):
                self.assertEqual(freshness.norm_date(value), '20261113')
        self.assertEqual(freshness.norm_date('2024-02-29'), '20240229')
        for value in (None, pd.NA, '', '2026-99-99', '2026-02-29',
                      '202611130', 'garbage20261113', '2026-11-13garbage'):
            with self.subTest(value=str(value)):
                self.assertEqual(freshness.norm_date(value), '')
                self.assertEqual(freshness.verdict(value, today=self.today)['status'], 'unknown')

    def test_future_date_is_unknown_and_cannot_stale_other_data(self):
        result = freshness.verdict('20270101', today=self.today)
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('晚于', result['reason'])
        self.assertEqual(freshness.verdict('20261113', reference_date='20270101',
                                         today=self.today)['status'], 'ok')

    def test_existing_freshness_rules(self):
        self.assertEqual(freshness.verdict('20261113', today=self.today)['status'], 'ok')
        self.assertEqual(freshness.verdict('20261112', reference_date='20261113',
                                         today=self.today)['status'], 'stale')
        self.assertEqual(freshness.verdict('20261110', reference_date='20261110',
                                         today=self.today)['status'], 'stale')


class SearchTests(unittest.TestCase):
    def test_actual_page_search_expressions(self):
        # Extract the real page expressions without starting Streamlit or network calls.
        source = Path(__file__).resolve().parents[1] / 'app.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        functions = {'render_stock_content', 'render_breakout_content', 'render_etf_content'}
        frame = pd.DataFrame({'ts_code': ['600000.SH', '510300.SH', None, '600001.SH'],
                              'name': ['示例[公司]', 'ETF(沪深)+', None, '普通公司']})
        expected = {'[': [True, False, False, False],
                    '(': [False, True, False, False],
                    '+': [False, True, False, False],
                    '.': [True, True, False, True],
                    ' sh ': [True, True, False, True],
                    'etf': [False, True, False, False],
                    'slgs': [True, False, False, False],
                    'PTGS': [False, False, False, True],
                    '不存在': [False] * 4}
        checked = set()
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef) or function.name not in functions:
                continue
            expressions = [node for node in ast.walk(function)
                           if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                           and node.func.id == 'stock_mask']
            self.assertEqual(len(expressions), 1)
            expression = compile(ast.Expression(expressions[0]), str(source), 'eval')
            for query, wanted in expected.items():
                with self.subTest(page=function.name, query=query):
                    result = eval(expression, {}, {'df': frame, 'show_df': frame,
                                                  'kw': query, 'kw_b': query, 'stock_mask': stock_mask})
                    self.assertEqual(result.tolist(), wanted)
            checked.add(function.name)
        self.assertEqual(checked, functions)

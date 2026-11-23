import ast
from pathlib import Path
import unittest

from navigation import DEFAULT_PAGE, NAV_GROUPS, canonical_page


class NavigationTests(unittest.TestCase):
    def test_every_route_is_reachable_once(self):
        source = Path(__file__).resolve().parents[1] / 'app.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        routes = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                    and node.left.id == 'page' and len(node.comparators) == 1
                    and isinstance(node.comparators[0], ast.Constant)):
                routes.append(node.comparators[0].value)
        pages = [page for description, entries in NAV_GROUPS.values() for page in entries]
        self.assertEqual(len(pages), 21)
        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(set(pages), set(routes))
        self.assertEqual(next(iter(NAV_GROUPS.values()))[1][0], '🛸 全市场看板')
        self.assertIn('🧩 题材跟踪', pages)
        self.assertIn('📊 名单表现', pages)
        self.assertIn('👁 观察记录', pages)
        self.assertNotIn('📈 命运K线 (人生运势)', pages)

    def test_legacy_and_unknown_routes_resolve_to_current_pages(self):
        self.assertEqual(canonical_page('🚨 核心龙头雷达'), '📐 强势股区间偏离')
        self.assertEqual(canonical_page('📏 黄金分割预测'), '📏 黄金分割参考位')
        self.assertEqual(canonical_page('📈 命运K线 (人生运势)'), DEFAULT_PAGE)
        self.assertEqual(canonical_page('📚 投资作业本'), '📚 机构持仓披露')
        self.assertEqual(canonical_page('不存在的页面'), DEFAULT_PAGE)

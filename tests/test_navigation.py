import ast
from pathlib import Path
import unittest

from navigation import NAV_GROUPS


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
        self.assertEqual(len(pages), 18)
        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(set(pages), set(routes))
        self.assertEqual(next(iter(NAV_GROUPS.values()))[1][0], '🛸 全市场看板')

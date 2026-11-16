import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import review_runtime


class ReviewRuntimeTests(unittest.TestCase):
    def test_cached_dependency_and_from_import_consumer_refresh_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "review_fixture_source.py"
            source.write_text('def text():\n    return "old"\n', encoding="utf-8")
            (root / "review_fixture_page.py").write_text(
                "from review_fixture_source import text\n"
                "def render():\n    return text()\n", encoding="utf-8",
            )
            names = ("review_fixture_source", "review_fixture_page")
            sys.path.insert(0, directory)
            try:
                page = importlib.import_module(names[1])
                self.assertEqual(page.render(), "old")
                stamp = source.stat()
                source.write_text('def text():\n    return "new"\n', encoding="utf-8")
                os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                with patch.object(review_runtime, "_ROOT", root), \
                     patch.object(review_runtime, "REVIEW_MODULES", names), \
                     patch.object(review_runtime, "_revision", None):
                    self.assertEqual(review_runtime.refresh_review_modules(), names)
                    self.assertEqual(page.render(), "new")
                    self.assertEqual(review_runtime.refresh_review_modules(), ())

                    source.write_text('def text():\n    return "v03"\n', encoding="utf-8")
                    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    self.assertEqual(review_runtime.refresh_review_modules(), names)
                    self.assertEqual(page.render(), "v03")

                    source.write_text("def broken(\n", encoding="utf-8")
                    with self.assertRaises(SyntaxError):
                        review_runtime.refresh_review_modules()
                    self.assertEqual(page.render(), "v03")
            finally:
                sys.path.remove(directory)
                for name in names:
                    sys.modules.pop(name, None)

    def test_bootstrap_refreshes_cached_helper_and_new_dependency(self):
        import ast
        import ladder_themes

        app_path = Path(__file__).resolve().parents[1] / "app.py"
        tree = ast.parse(app_path.read_text(encoding="utf-8"))
        end = next(i for i, node in enumerate(tree.body)
                   if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name)
                   and node.value.func.id == "refresh_review_modules")
        bootstrap = compile(ast.Module(body=tree.body[:end + 1], type_ignores=[]), str(app_path), "exec")
        lock = review_runtime._lock
        with patch.object(review_runtime, "REVIEW_MODULES", ("data_freshness",)), \
             patch.object(review_runtime, "_bootstrap_source", None, create=True), \
             patch.object(ladder_themes, "SOURCES", {}):
            exec(bootstrap, {})
            self.assertIn("ladder_themes", review_runtime.REVIEW_MODULES)
            self.assertIn("ths_public", ladder_themes.SOURCES)
            self.assertIs(review_runtime._lock, lock)
            with patch.object(importlib, "reload", wraps=importlib.reload) as reload:
                exec(bootstrap, {})
                reload.assert_not_called()

    def test_bootstrap_precedes_review_imports(self):
        import ast

        app_path = Path(__file__).resolve().parents[1] / "app.py"
        tree = ast.parse(app_path.read_text(encoding="utf-8"))
        bootstrap = next(
            node.lineno for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "refresh_review_modules"
        )
        consumers = [node.lineno for node in tree.body if isinstance(node, ast.ImportFrom)
                     and node.module in review_runtime.REVIEW_MODULES]
        self.assertTrue(consumers)
        self.assertTrue(all(bootstrap < line for line in consumers))


if __name__ == "__main__":
    unittest.main()

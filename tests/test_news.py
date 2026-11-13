import unittest
from unittest.mock import patch
from page_news import parse_news, fetch_news


class NewsTests(unittest.TestCase):
    def test_deduplicate_and_validate_links(self):
        row = {"code": "123", "title": "<b>新闻</b>", "showTime": "2026-11-13 12:00"}
        rows = parse_news({"data": {"fastNewsList": [row, row, {"code": "../bad", "title": "bad"}]}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "新闻")
        self.assertEqual(rows[0]["url"], "https://finance.eastmoney.com/a/123.html")

    def test_empty_is_not_success(self):
        with self.assertRaises(ValueError):
            parse_news({})

    def test_network_failure(self):
        fetch_news.clear()
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            rows, stamp, error = fetch_news()
        self.assertEqual(rows, [])
        self.assertTrue(error)
        fetch_news.clear()

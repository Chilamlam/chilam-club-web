"""Missing optional backend configuration must not crash public pages."""
import unittest
from unittest.mock import patch

import database


class DatabaseConfigTests(unittest.TestCase):
    def test_invalid_configuration_does_not_send_requests(self):
        for config in (("", ""), ("", "key"), ("https://example.invalid", ""),
                       ("example.invalid", "key"), ("file:///data", "key")):
            with self.subTest(config=config), \
                    patch.object(database, "_get_config", return_value=config), \
                    patch.object(database.urllib.request, "urlopen") as send:
                self.assertIsNone(database._supabase_request("GET", "site_settings"))
                data, error = database._supabase_request(
                    "GET", "site_settings", return_error=True)
                self.assertIsNone(data)
                self.assertEqual(error["code"], "configuration_error")
                send.assert_not_called()

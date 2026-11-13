"""Offline regression checks for session token validation."""
import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import auth


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.secret = patch.object(auth, "JWT_SECRET", "test-only-session-secret")
        self.secret.start()
        self.addCleanup(self.secret.stop)

    def signed(self, payload, header=None):
        header = {"alg": "HS256", "typ": "JWT"} if header is None else header
        message = ".".join(auth._b64url_encode(json.dumps(value).encode())
                           for value in (header, payload))
        signature = hmac.new(auth.JWT_SECRET.encode(), message.encode(),
                             hashlib.sha256).digest()
        return message + "." + auth._b64url_encode(signature)

    def test_round_trip_and_utc_timestamps(self):
        before = datetime.now(timezone.utc).timestamp()
        token = auth.create_jwt_token(7, "test@example.invalid", True)
        payload = auth.decode_jwt_token(token)
        self.assertEqual(payload["user_id"], 7)
        self.assertTrue(payload["is_admin"])
        self.assertLessEqual(before - 1, payload["iat"])
        self.assertLessEqual(payload["iat"], datetime.now(timezone.utc).timestamp())
        self.assertEqual(payload["exp"] - payload["iat"],
                         auth.JWT_EXPIRATION_HOURS * 3600)

    def test_malformed_input_never_raises(self):
        for token in (None, 123, "", "a", "a.b", "a.b.c", "a.b.é", "a.b.===="):
            with self.subTest(token=token):
                self.assertIsNone(auth.decode_jwt_token(token))

    def test_tampering_rejected(self):
        token = auth.create_jwt_token(7, "test@example.invalid")
        header, _, signature = token.split(".")
        payload = auth._b64url_encode(b'{"user_id":1,"is_admin":true}')
        self.assertIsNone(auth.decode_jwt_token(f"{header}.{payload}.{signature}"))

    def test_expiration_required_and_finite(self):
        for expires in (None, True, "9999999999", [], {}, 0, -1,
                        float("nan"), float("inf"), float("-inf")):
            with self.subTest(expires=expires):
                self.assertIsNone(auth.decode_jwt_token(self.signed({"exp": expires})))
        self.assertIsNone(auth.decode_jwt_token(self.signed({"user_id": 7})))

    def test_expiration_boundary(self):
        with patch.object(auth, "datetime") as clock:
            clock.now.return_value = datetime.fromtimestamp(1000, timezone.utc)
            self.assertIsNone(auth.decode_jwt_token(self.signed({"exp": 1000})))
            self.assertIsNotNone(auth.decode_jwt_token(self.signed({"exp": 1001})))

    def test_non_object_payload_rejected(self):
        for payload in ([], ["exp"], "exp", 123, None):
            with self.subTest(payload=payload):
                self.assertIsNone(auth.decode_jwt_token(self.signed(payload)))

    def test_header_algorithm_checked(self):
        for header in ([], None, {"alg": "none"}, {"alg": "HS512"}, {}):
            if header is None:
                continue
            with self.subTest(header=header):
                self.assertIsNone(auth.decode_jwt_token(
                    self.signed({"exp": 9999999999}, header)))


if __name__ == "__main__":
    unittest.main()

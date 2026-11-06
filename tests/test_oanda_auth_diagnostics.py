"""Read-only OANDA endpoint and 401-stage diagnosis; no real secrets or API requests."""
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import oanda_data as market


class OandaAuthDiagnosticsTests(unittest.TestCase):
    def test_explicit_live_endpoint_is_used_without_fallback(self):
        with patch.dict(os.environ, {"OANDA_API_TOKEN": "synthetic", "OANDA_ENVIRONMENT": "live"}), \
             patch.object(market, "urlopen", return_value=io.BytesIO(b'{"accounts":[]}')) as opened:
            self.assertEqual(market.request("/v3/accounts"), {"accounts": []})
            self.assertEqual(opened.call_args.args[0].full_url,
                             "https://api-fxtrade.oanda.com/v3/accounts")

    def test_invalid_environment_blocks_network(self):
        with patch.dict(os.environ, {"OANDA_API_TOKEN": "synthetic", "OANDA_ENVIRONMENT": "demo"}), \
             patch.object(market, "urlopen") as opened:
            with self.assertRaisesRegex(RuntimeError, "OANDA_INVALID_ENVIRONMENT"):
                market.request("/v3/accounts")
            opened.assert_not_called()

    def test_401_includes_only_redacted_request_stage_and_is_not_retried(self):
        denied = HTTPError("https://example.invalid/mock", 401, "Unauthorized", {}, None)
        with patch.dict(os.environ, {"OANDA_API_TOKEN": "synthetic", "OANDA_ENVIRONMENT": "practice"}), \
             patch.object(market, "urlopen", side_effect=denied) as opened:
            with self.assertRaisesRegex(RuntimeError, r"^OANDA_HTTP_401_INSTRUMENTS$"):
                market.request("/v3/accounts/SENSITIVE_TEST_ID/instruments")
            self.assertEqual(opened.call_count, 1)

    def test_denied_account_is_differentiated_from_invalid_token(self):
        with patch.dict(os.environ, {"OANDA_ACCOUNT_ID": "synthetic-account", "OANDA_ENVIRONMENT": "live"}), \
             patch.object(market, "request", side_effect=[
                 RuntimeError("OANDA_HTTP_401_INSTRUMENTS"),
                 {"accounts": [{"id": "different-account"}]},
             ]) as req:
            with self.assertRaisesRegex(RuntimeError, "^OANDA_ACCOUNT_NOT_ACCESSIBLE_LIVE$"):
                market.collect()
            self.assertEqual(req.call_count, 2)

    def test_token_rejection_after_account_probe(self):
        with patch.dict(os.environ, {"OANDA_ACCOUNT_ID": "synthetic-account", "OANDA_ENVIRONMENT": "practice"}), \
             patch.object(market, "request", side_effect=[
                 RuntimeError("OANDA_HTTP_401_INSTRUMENTS"),
                 RuntimeError("OANDA_HTTP_401_ACCOUNTS"),
             ]) as req:
            with self.assertRaisesRegex(RuntimeError, "^OANDA_TOKEN_REJECTED_PRACTICE$"):
                market.collect()
            self.assertEqual(req.call_count, 2)

    def test_probe_timeout_is_not_mislabelled_invalid_token(self):
        with patch.dict(os.environ, {"OANDA_ACCOUNT_ID": "synthetic-account"}), \
             patch.object(market, "request", side_effect=[
                 RuntimeError("OANDA_HTTP_401_INSTRUMENTS"),
                 RuntimeError("OANDA_HTTP_504_ACCOUNTS"),
             ]):
            with self.assertRaisesRegex(RuntimeError, "^OANDA_HTTP_504_ACCOUNTS$"):
                market.collect()


if __name__ == "__main__":
    unittest.main()

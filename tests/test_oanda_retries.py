"""Bounded retry regression tests for OANDA's read-only GET collector."""
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import oanda_data as collector


class OandaGatewayRetryTests(unittest.TestCase):
    def test_retries_one_504_then_returns_data(self):
        gateway = HTTPError("https://api-fxpractice.oanda.com/mock", 504,
                            "Gateway timeout", {}, None)
        with patch.dict(os.environ, {"OANDA_API_TOKEN": "fake-test-token"}), \
             patch.object(collector, "TRANSIENT_RETRIES_LEFT", 1), \
             patch.object(collector, "urlopen",
                          side_effect=[gateway, io.BytesIO(b'{"prices":[]}')]) as fetch, \
             patch.object(collector.time, "sleep") as delay:
            self.assertEqual(collector.request("/mock"), {"prices": []})
            self.assertEqual(fetch.call_count, 2)
            delay.assert_called_once_with(0.5)
            self.assertEqual(collector.TRANSIENT_RETRIES_LEFT, 0)

    def test_no_retry_for_authentication_error(self):
        denied = HTTPError("https://api-fxpractice.oanda.com/mock", 401,
                           "Unauthorized", {}, None)
        with patch.dict(os.environ, {"OANDA_API_TOKEN": "fake-test-token"}), \
             patch.object(collector, "TRANSIENT_RETRIES_LEFT", 8), \
             patch.object(collector, "urlopen", side_effect=denied) as fetch, \
             patch.object(collector.time, "sleep") as delay:
            with self.assertRaisesRegex(RuntimeError, "OANDA_HTTP_401"):
                collector.request("/mock")
            self.assertEqual(fetch.call_count, 1)
            delay.assert_not_called()

    def test_retry_budget_exhaustion_preserves_failure(self):
        gateway = HTTPError("https://api-fxpractice.oanda.com/mock", 504,
                            "Gateway timeout", {}, None)
        with patch.dict(os.environ, {"OANDA_API_TOKEN": "fake-test-token"}), \
             patch.object(collector, "TRANSIENT_RETRIES_LEFT", 0), \
             patch.object(collector, "urlopen", side_effect=gateway) as fetch:
            with self.assertRaisesRegex(RuntimeError, "OANDA_HTTP_504"):
                collector.request("/mock")
            self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()

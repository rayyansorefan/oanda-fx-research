"""401/403 observation without changing hashed collector/forward trading rules."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import oanda_auth_diagnostics as auth


class AuthDiagnosticsTests(unittest.TestCase):
    def test_stage_redacts_account_ids_and_pair_names(self):
        self.assertEqual(auth.stage("/v3/accounts/sensitive-id/instruments"), "INSTRUMENTS")
        self.assertEqual(auth.stage("/v3/instruments/EUR_USD/candles"), "CANDLES")
        self.assertEqual(auth.stage("/v3/accounts/sensitive-id/pricing"), "PRICING")
        self.assertEqual(auth.stage("/v3/accounts"), "ACCOUNTS")

    def test_401_is_observed_once_and_does_not_retry(self):
        request = Mock(side_effect=RuntimeError("OANDA_HTTP_401"))
        module = SimpleNamespace(request=request)
        obs = auth.Observer()
        with obs.capture(module):
            with self.assertRaisesRegex(RuntimeError, "^OANDA_HTTP_401_INSTRUMENTS$"):
                module.request("/v3/accounts/PRIVATE_ID/instruments")
        self.assertEqual(request.call_count, 1)
        self.assertIs(module.request, request)
        self.assertEqual(obs.failures, ["OANDA_HTTP_401_INSTRUMENTS"])
        self.assertNotIn("PRIVATE_ID", str(obs.failures))

    def test_masked_quote_auth_error_is_still_reported(self):
        request = Mock(side_effect=RuntimeError("OANDA_HTTP_401"))
        module = SimpleNamespace(request=request)
        obs = auth.Observer()
        with obs.capture(module):
            try:
                module.request("/v3/accounts/secret/pricing")
            except RuntimeError:
                pass  # frozen quote batch can mask an individual failure
        self.assertEqual(obs.error_for(RuntimeError("OANDA_NO_USABLE_FX_QUOTES")),
                         "OANDA_HTTP_401_PRICING")

    def test_token_rejection_is_distinguished_by_second_endpoint(self):
        module = SimpleNamespace(request=Mock(side_effect=RuntimeError("OANDA_HTTP_401")))
        obs = auth.Observer()
        self.assertEqual(obs.probe(module, "OANDA_HTTP_401_INSTRUMENTS"),
                         "OANDA_TOKEN_REJECTED_PRACTICE")
        module.request.assert_called_once_with("/v3/accounts")

    def test_accessible_token_inaccessible_account(self):
        module = SimpleNamespace(request=Mock(return_value={"accounts": [{"id": "other"}]}))
        with patch.dict(os.environ, {"OANDA_ACCOUNT_ID": "hidden"}):
            self.assertEqual(auth.Observer().probe(module, "OANDA_HTTP_401_INSTRUMENTS"),
                             "OANDA_ACCOUNT_NOT_ACCESSIBLE_PRACTICE")

    def test_distinct_504_probe_does_not_claim_invalid_token(self):
        module = SimpleNamespace(request=Mock(side_effect=RuntimeError("OANDA_HTTP_504")))
        self.assertEqual(auth.Observer().probe(module, "OANDA_HTTP_401_INSTRUMENTS"),
                         "OANDA_ACCOUNT_PROBE_INCONCLUSIVE")

    def test_other_http_failure_does_not_trigger_probe(self):
        module = SimpleNamespace(request=Mock())
        self.assertEqual(auth.Observer().probe(module, "OANDA_HTTP_504_PRICING"),
                         "OANDA_HTTP_504_PRICING")
        module.request.assert_not_called()

    def test_safe_host_reports_actual_frozen_endpoint_only(self):
        self.assertEqual(auth.safe_host(SimpleNamespace(BASE="https://api-fxpractice.oanda.com")),
                         "practice")
        self.assertEqual(auth.safe_host(SimpleNamespace(BASE="https://api-fxtrade.oanda.com")),
                         "NONSTANDARD")


if __name__ == "__main__":
    unittest.main()

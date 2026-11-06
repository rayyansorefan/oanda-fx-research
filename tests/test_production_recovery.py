"""Fault injection: retain source evidence, fail closed, never fake observations."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

# The independently scheduled fast job runs each frozen safety suite in its
# own Python process. This module must resolve OANDA's legacy absolute imports
# without relying on other test modules having modified sys.path first.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts import oanda_data as market
from scripts import fast_entry_scan as fast
from scripts import production_recovery as recovery


class RecoveryTests(unittest.TestCase):
    def test_partial_collection_failure_restores_entire_source_set(self):
        with tempfile.TemporaryDirectory() as d:
            previous = Path.cwd()
            os.chdir(d)
            try:
                originals = {p: ('old-' + p).encode() for p in
                             ('data/latest.json', 'data/summary.json',
                              'data/universe.json', 'data/pairs/EUR_USD.json')}
                for p, content in originals.items():
                    Path(p).parent.mkdir(parents=True, exist_ok=True)
                    Path(p).write_bytes(content)
                def fail():
                    Path('data/latest.json').write_text('partial')
                    Path('data/pairs/GBP_USD.json').write_text('partial')
                    raise RuntimeError('OANDA_HTTP_504')
                with patch.object(market, 'collect', side_effect=fail), patch.dict(os.environ, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}):
                    self.assertEqual(recovery.run_collection(), 1)
                for p, content in originals.items():
                    self.assertEqual(Path(p).read_bytes(), content)
                self.assertFalse(Path('data/pairs/GBP_USD.json').exists())
                record = json.loads(Path('data/collection_attempts/123-1.json').read_text())
                self.assertEqual(record['error'], 'OANDA_HTTP_504')
                self.assertTrue(record['last_valid_source_preserved'])
            finally:
                os.chdir(previous)

    def test_one_bounded_recovery_succeeds_after_intermitttent_accounts_401(self):
        with tempfile.TemporaryDirectory() as d:
            previous = Path.cwd()
            os.chdir(d)
            try:
                Path("data/summary.json").parent.mkdir(parents=True)
                Path("data/summary.json").write_text("last-good-summary")
                calls = []

                def flaky():
                    calls.append(len(calls) + 1)
                    if len(calls) == 1:
                        Path("data/summary.json").write_text("bad-partial-summary")
                        raise RuntimeError("OANDA_HTTP_401_ACCOUNTS")
                    # Must see the restored bytes before a second collection.
                    self.assertEqual(Path("data/summary.json").read_text(),
                                     "last-good-summary")
                    Path("data/summary.json").write_text("fresh-verified-summary")

                with patch.object(market, "collect", side_effect=flaky), \
                     patch.object(recovery.time, "sleep") as pause:
                    self.assertEqual(recovery.run_collection(), 0)
                self.assertEqual(calls, [1, 2])
                pause.assert_called_once_with(2)
                self.assertEqual(Path("data/summary.json").read_text(),
                                 "fresh-verified-summary")
                receipt = json.loads(Path("data/collection_attempt.json").read_text())
                self.assertEqual(receipt["status"], "COLLECTED")
                self.assertEqual(receipt["collection_tries"], 2)
                self.assertTrue(receipt["recovered_on_second_try"])
                self.assertEqual(receipt["first_attempt_error"],
                                 "OANDA_HTTP_401_ACCOUNTS")
            finally:
                os.chdir(previous)

    def test_persistent_accounts_401_fails_closed_and_preserves_last_source(self):
        with tempfile.TemporaryDirectory() as d:
            old = Path.cwd()
            os.chdir(d)
            try:
                path = Path("data/summary.json")
                path.parent.mkdir(parents=True)
                path.write_text("last-valid-source")
                with patch.object(market, "collect",
                                  side_effect=RuntimeError("OANDA_HTTP_401_ACCOUNTS")) as collect, \
                     patch.object(recovery.time, "sleep") as pause:
                    self.assertEqual(recovery.run_collection(), 1)
                self.assertEqual(collect.call_count, 2)
                pause.assert_called_once_with(2)
                self.assertEqual(path.read_text(), "last-valid-source")
                receipt = json.loads(Path("data/collection_attempt.json").read_text())
                self.assertEqual(receipt["status"], "DATA_UNAVAILABLE")
                self.assertEqual(receipt["collection_tries"], 2)
                self.assertFalse(receipt["recovered_on_second_try"])
                self.assertEqual(receipt["error"], "OANDA_HTTP_401_ACCOUNTS")
            finally:
                os.chdir(old)

    def test_403_and_missing_token_never_trigger_recovery(self):
        for error in ("OANDA_HTTP_403_ACCOUNTS", "Missing OANDA_API_TOKEN secret"):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as d:
                previous = Path.cwd()
                os.chdir(d)
                try:
                    with patch.object(market, "collect", side_effect=RuntimeError(error)) as collect, \
                         patch.object(recovery.time, "sleep") as pause:
                        self.assertEqual(recovery.run_collection(), 1)
                    collect.assert_called_once()
                    pause.assert_not_called()
                    report = json.loads(Path("data/collection_attempt.json").read_text())
                    self.assertEqual(report["collection_tries"], 1)
                    self.assertFalse(report["recovered_on_second_try"])
                finally:
                    os.chdir(previous)

    def test_fast_exception_replaces_success_audit_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as d, patch.object(fast, 'ROOT', Path(d)), patch.object(fast, 'FAST_AUDIT', Path(d)/'audit.json'), patch.object(fast, 'perform', side_effect=RuntimeError('OANDA_HTTP_401')):
            history = Path(d)/'history.json'
            history.write_text('previous immutable observation')
            self.assertEqual(recovery.run_fast(), 1)
            report = json.loads((Path(d)/'audit.json').read_text())
            self.assertEqual(report['status'], 'FAST_MARKET_DATA_UNAVAILABLE')
            self.assertEqual(report['error'], 'OANDA_HTTP_401')
            self.assertEqual(report['analysed'], 0)
            self.assertEqual(history.read_text(), 'previous immutable observation')

    def test_stale_source_is_nonzero_not_workflow_success(self):
        with tempfile.TemporaryDirectory() as d, patch.object(fast, 'FAST_AUDIT', Path(d)/'audit.json'), patch.object(fast, 'perform', return_value={'status': 'STALE_HOURLY_SOURCE'}):
            self.assertEqual(recovery.run_fast(), 1)


if __name__ == '__main__':
    unittest.main()

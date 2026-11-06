"""Fault injection: retain source evidence, fail closed, never fake observations."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

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

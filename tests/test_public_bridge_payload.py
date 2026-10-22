"""Tests for preparing a safe public bridge artifact without a GitHub PAT."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import export_public_dashboard as exp

class NoPATBridgeTests(unittest.TestCase):
    def test_whitelisted_feed_prepared_without_token(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"data").mkdir()
            sample_summary={"collected_at":"2026-10-21T19:30:00Z","status":"OK",
                "coverage":{"oanda_account_fx_instruments_discovered":1,
                    "oanda_pairs_with_usable_live_quotes":1,
                    "oanda_pairs_with_m15_h1_indicators":1,
                    "detailed_audit_pairs":[]},
                "pairs":{"EUR_USD":{"quote":{"time":"2026-10-21T19:29:00Z",
                    "bid":1.1,"ask":1.1001,"spread_pips":1.0,
                    "pip_size":0.0001,"tradeable":True,
                    "account_id":"HIDDEN_PRIVATE_ACCOUNT"},
                    "frames":{"M15":{"completed_at":"2026-10-21T19:15:00Z",
                                       "indicators":{"ema20":1.1}}}}},
                "ranked_review_candidates":[]}
            sample_news={"collected_at":"2026-10-21T19:30:00Z",
                "status":"PARTIAL","sources":{},"calendar":{"events":[]},
                "recent_material_headlines":[]}
            files={"data/summary.json":sample_summary,"data/news_latest.json":sample_news,
                   "data/latest.json":{"collected_at":sample_summary["collected_at"],
                                       "pairs":{"EUR_USD":{"frames":{}}}},
                   "signals.json":{"signals":[]},"data/hourly_assessment.json":{}}
            for name,data in files.items():
                (root/name).write_text(json.dumps(data))
            old=Path.cwd()
            import os
            try:
                os.chdir(root)
                with patch.object(exp,"PUBLIC_PAYLOAD_PATH",root/"data/public_dashboard_payload.json"),\
                     patch.object(exp,"STATUS_PATH",root/"data/public_dashboard_status.json"),\
                     patch.object(exp,"publish") as publisher,\
                     patch.dict(os.environ,{"FX_DASHBOARD_DEPLOY_TOKEN":""}):
                    self.assertEqual(exp.main(),1)
                    publisher.assert_not_called()
                payload=json.loads((root/"data/public_dashboard_payload.json").read_text())
                status=json.loads((root/"data/public_dashboard_status.json").read_text())
                self.assertEqual(status["status"],"NOT_CONFIGURED")
                self.assertEqual(payload["schema"],1)
                self.assertEqual(payload["snapshot_at"],sample_summary["collected_at"])
                self.assertNotIn("HIDDEN_PRIVATE_ACCOUNT",json.dumps(payload))
                self.assertEqual(payload["pairs"]["EUR_USD"]["quote"]["bid"],1.1)
            finally:
                os.chdir(old)
    def test_bad_inputs_never_prepare_public_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"data").mkdir()
            old=Path.cwd()
            import os
            try:
                os.chdir(root)
                for file in ("data/summary.json","data/news_latest.json","signals.json","data/hourly_assessment.json"):
                    (root/file).write_text("{}")
                with patch.object(exp,"PUBLIC_PAYLOAD_PATH",root/"data/public_dashboard_payload.json"),\
                     patch.object(exp,"STATUS_PATH",root/"data/public_dashboard_status.json"),\
                     patch.dict(os.environ,{"FX_DASHBOARD_DEPLOY_TOKEN":""}):
                    self.assertNotEqual(exp.main(),0)
                self.assertFalse((root/"data/public_dashboard_payload.json").exists())
            finally:
                os.chdir(old)

class VerifiedPublicationTests(unittest.TestCase):
    def test_older_payload_cannot_overwrite_newer_public_feed(self):
        candidate = {"snapshot_at": "2026-10-22T15:00:00Z"}
        remote = {"snapshot_at": "2026-10-22T16:00:00Z"}
        with patch.object(exp, "_existing_public_feed", return_value=("sha", remote)), \\
             patch.object(exp.urllib.request, "urlopen") as put:
            self.assertEqual(exp.publish(candidate, "dummy-token"), "SUPERSEDED")
            put.assert_not_called()

    def test_published_commit_requires_exact_remote_readback(self):
        import io
        candidate = {"snapshot_at": "2026-10-22T16:00:00Z",
                     "published_at": "2026-10-22T16:00:05Z", "schema": 1}
        old = {"snapshot_at": "2026-10-22T15:00:00Z"}
        saved_response = io.BytesIO(b'{"commit":{"sha":"abc"}}')
        with patch.object(exp, "_existing_public_feed",
                          side_effect=[("old-sha", old), ("new-sha", candidate)]), \\
             patch.object(exp.urllib.request, "urlopen", return_value=saved_response):
            self.assertEqual(exp.publish(candidate, "dummy-token"), "PUBLISHED")


if __name__=="__main__":
    unittest.main()

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import source_freshness_fallback as fallback


class FullSourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.summary=root/"summary.json"
        self.universe=root/"universe.json"
        p1=patch.object(fallback,"SOURCE",self.summary)
        p2=patch.object(fallback,"UNIVERSE",self.universe)
        p1.start();p2.start()
        self.addCleanup(p1.stop);self.addCleanup(p2.stop)

    def write(self,stamp,status="OK"):
        self.summary.write_text(json.dumps({
            "status":status,"collected_at":stamp,"pairs":{"EUR_USD":{}}}))
        self.universe.write_text(json.dumps({"collected_at":stamp}))

    def test_fresh_observation_skips_expensive_recollection(self):
        now=datetime.now(timezone.utc)
        self.write(now.isoformat())
        report=fallback.collect(now=now,invoke=lambda:self.fail("unexpected API request"))
        self.assertFalse(report["refreshed"])
        self.assertEqual(report["status"],"EXISTING_FULL_SOURCE_FRESH")

    def test_stale_observation_recovers_only_when_new_source_validated(self):
        now=datetime.now(timezone.utc)
        old=(now-timedelta(hours=2)).isoformat()
        self.write(old)
        def invoke():
            self.write(datetime.now(timezone.utc).isoformat())
            return 0
        report=fallback.collect(now=now,invoke=invoke)
        self.assertTrue(report["refreshed"])
        self.assertEqual(report["status"],"FRESH_FULL_SOURCE_RECOVERED")

    def test_invalid_recollection_refuses_success_and_retains_old(self):
        now=datetime.now(timezone.utc)
        old=(now-timedelta(hours=2)).isoformat()
        self.write(old)
        called=[]
        def invoke():
            self.write(datetime.now(timezone.utc).isoformat(),"DATA_UNAVAILABLE")
            return 1
        def restore():
            called.append(True)
            self.write(old)
        with self.assertRaisesRegex(RuntimeError,"NO_FRESH_OANDA_SOURCE"):
            fallback.collect(now=now,invoke=invoke,restore=restore)
        self.assertEqual(called,[True])
        self.assertEqual(json.loads(self.summary.read_text())["collected_at"],old)

    def test_timestamp_match_required(self):
        now=datetime.now(timezone.utc)
        self.write(now.isoformat())
        self.universe.write_text(json.dumps({"collected_at":"2020-01-01T00:00:00Z"}))
        self.assertFalse(fallback.valid(json.loads(self.summary.read_text()),
                                        json.loads(self.universe.read_text()),now))


if __name__=="__main__":
    unittest.main()

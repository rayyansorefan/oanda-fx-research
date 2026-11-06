"""Acceptance tests for persisted 15-minute dispatch observations."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import fast_dispatch_proof as proof
from scripts import monitoring_health as monitor

START = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)


def record(number, minute, *, status=proof.COMPLETE, event="workflow_dispatch", quote=True):
    at = START + timedelta(minutes=minute)
    source = at - timedelta(seconds=20)
    rows = {"AUD_USD": {
        "quote_at": (source - timedelta(seconds=5)).isoformat(),
        "completed_candle_open_times": {
            "M5": (source-timedelta(minutes=5)).isoformat(),
            "M15": (source-timedelta(minutes=15)).isoformat()}}} if quote else {}
    return {"run_id": str(number), "event": event, "checked_at": at.isoformat(),
            "source_at": source.isoformat(), "status": status,
            "analysed": len(rows), "pair_evidence": rows}


class FastDispatchProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.dir = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, name, row):
        (self.dir / name).write_text(json.dumps(row))

    def prove(self, at=START + timedelta(hours=2)):
        return proof.summarize(self.dir, at)

    def test_no_receipts_is_explicitly_unverified(self):
        result = self.prove()
        self.assertEqual(result["status"], "NO_DISPATCH_RECEIPTS")
        self.assertFalse(result["four_consecutive_event_observations"])
        self.assertFalse(result["external_scheduler_origin_verified"])

    def test_only_four_consecutive_actual_pair_observations_count(self):
        for i, minutes in enumerate([0, 15, 30, 45], 1):
            self.save(str(i)+".json", record(i, minutes))
        result = self.prove()
        self.assertEqual(result["status"], "FOUR_EVENT_OBSERVATIONS_SEEN_ORIGIN_UNVERIFIED")
        self.assertEqual(result["actual_intervals_seconds"], [900, 900, 900])
        self.assertEqual(result["last_four_run_ids"], ["1", "2", "3", "4"])
        self.assertFalse(result["sustained_reliability_verified"])

    def test_push_and_schedule_cannot_fake_independent_dispatch(self):
        for i, minutes in enumerate([0, 15, 30, 45], 1):
            self.save(str(i)+".json", record(i, minutes, event="push" if i<3 else "schedule"))
        self.assertEqual(self.prove()["dispatch_attempt_receipts"], 0)

    def test_failed_or_empty_quote_breaks_streak(self):
        for i, minutes in enumerate([0, 15, 30, 45, 60], 1):
            self.save(str(i)+".json", record(i, minutes,
                      status="FAST_MARKET_DATA_UNAVAILABLE" if i==3 else proof.COMPLETE,
                      quote=i!=4))
        result = self.prove()
        self.assertFalse(result["four_consecutive_event_observations"])
        self.assertEqual(result["latest_consecutive_valid_event_observations"], 1)
        self.assertEqual(result["valid_new_market_observations"], 3)

    def test_duplicate_source_and_wide_gap_do_not_verify_cadence(self):
        for i, minutes in enumerate([0, 15, 50, 65], 1):
            self.save(str(i)+".json", record(i, minutes))
        result = self.prove()
        self.assertFalse(result["four_consecutive_event_observations"])
        self.assertEqual(result["latest_consecutive_valid_event_observations"], 2)

    def test_two_sessions_still_do_not_prove_external_provider_origin(self):
        for day in (0, 3):  # Friday and Monday; skip weekend.
            for i, minutes in enumerate([0, 15, 30, 45], 1):
                row = record(day*10+i, minutes+day*1440)
                self.save(str(day*10+i)+".json", row)
        result = self.prove(START+timedelta(days=3,hours=1))
        self.assertTrue(result["two_session_observation_evidence"])
        self.assertEqual(len(result["sessions_with_four_event_observations"]), 2)
        self.assertFalse(result["external_scheduler_origin_verified"])
        self.assertFalse(result["sustained_reliability_verified"])

    def test_future_and_weekend_do_not_count(self):
        self.save("future.json", record(1, 300))
        self.save("weekend.json", record(2, 1440))
        result = self.prove()
        self.assertEqual(result["valid_new_market_observations"], 0)

    def test_monitoring_health_includes_explicit_unverified_status(self):
        with patch.object(monitor, "ROOT", self.dir):
            result = monitor.build(START+timedelta(minutes=45), remote=False)
        self.assertEqual(result["fast_dispatch_evidence"]["status"], "NO_DISPATCH_RECEIPTS")


if __name__ == "__main__":
    unittest.main()

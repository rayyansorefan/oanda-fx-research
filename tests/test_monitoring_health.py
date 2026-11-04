import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock
from scripts import monitoring_health as m

NOW = datetime(2026, 11, 4, 16, 50, tzinfo=timezone.utc)


class MonitoringTests(unittest.TestCase):
    def test_real_scheduled_intervals_exclude_push_and_dispatch(self):
        runs = [{"id":i, "created_at":t, "path":"fast", "event":e} for i,t,e in (
            (1,"2026-11-04T15:02:00Z","schedule"),
            (2,"2026-11-04T15:17:00Z","push"),
            (3,"2026-11-04T15:32:00Z","schedule"),
            (4,"2026-11-04T15:47:00Z","workflow_dispatch"))]
        result = m.schedule_evidence(runs, "fast")
        self.assertEqual(result["run_ids"], [1,3])
        self.assertEqual(result["interval_seconds"], [1800])

    def test_failed_attempt_cannot_be_fresh_observation(self):
        for status in ("STALE_HOURLY_SOURCE", "FAST_MARKET_DATA_UNAVAILABLE", "NO_ELIGIBLE_PAIRS"):
            self.assertFalse(m.fast_valid({"status":status,"checked_at":NOW.isoformat()}, {}, NOW))

    def test_fresh_success_requires_matching_snapshot_and_pairs(self):
        stamp = "2026-11-04T16:47:00Z"
        audit = {"status":m.FAST_OK,"source_at":stamp,"analysed":1}
        self.assertTrue(m.fast_valid(audit, {"collected_at":stamp,"pairs":{"EUR_USD":{}}}, NOW))
        self.assertFalse(m.fast_valid(audit, {"collected_at":stamp,"pairs":{}}, NOW))
        self.assertFalse(m.fast_valid(audit, {"collected_at":"2026-11-04T12:00:00Z","pairs":{"EUR_USD":{}}}, NOW))

    def test_dst_and_trading_day_targets(self):
        summer = datetime(2026,7,6,6,10,tzinfo=timezone.utc)
        self.assertTrue(m.active(summer))
        self.assertEqual([t.isoformat() for t in m.slots(summer)], ["2026-07-06T06:02:00+00:00"])
        self.assertFalse(m.active(datetime(2026,11,7,10,tzinfo=timezone.utc)))
        self.assertEqual(m.slots(datetime(2026,11,7,10,tzinfo=timezone.utc)), [])

    def test_missing_history_reports_gaps_without_creating_observations(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m,"ROOT",Path(d)):
            report = m.build(NOW,remote=False)
            self.assertEqual(report["status"], "DEGRADED")
            self.assertEqual(report["early_observation_count"], 0)
            self.assertGreater(report["consecutive_unobserved_fast_targets"], 0)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_zero_exit_scanner_without_observations_is_failed_recovery(self):
        with patch.object(m,"read",return_value={}), patch.object(m.subprocess,"run",return_value=Mock(returncode=0)) as run:
            self.assertEqual(m.fast_if_due(NOW),1)
            self.assertEqual(run.call_count,1)

    def test_recent_valid_scan_skips_network_and_preserves_state(self):
        stamp = "2026-11-04T16:47:00Z"
        rows = {"data/fast_entry_audit.json":{"status":m.FAST_OK,"source_at":stamp,"analysed":1},
                "data/fast_latest.json":{"collected_at":stamp,"pairs":{"EUR_USD":{}}}}
        with patch.object(m,"read",side_effect=lambda p: rows[p]), patch.object(m.subprocess,"run") as run:
            self.assertEqual(m.fast_if_due(NOW),0)
            run.assert_not_called()

    def test_remote_errors_are_explicit(self):
        with patch.object(m,"read",return_value={}), patch.object(m,"github",side_effect=RuntimeError()):
            self.assertEqual(m.build(NOW)["scheduler_verification"],"UNAVAILABLE")

    def test_one_observation_cannot_cover_two_targets(self):
        rows = {"data/fast_entry_history.json":{"observations":[
            {"at":"2026-11-04T16:49:00Z","pairs":["EUR_USD"]}]}}
        with patch.object(m,"read",side_effect=lambda p,*a:rows.get(p,{})):
            report = m.build(datetime(2026,11,4,16,55,tzinfo=timezone.utc),remote=False)
            self.assertIn("2026-11-04T16:32:00+00:00",report["unobserved_fast_targets_today"])
            self.assertNotIn("2026-11-04T16:47:00+00:00",report["unobserved_fast_targets_today"])

"""Fast FX scan: offline safety, ordering and immutable-event regression tests."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from scripts import fast_entry_scan as fast

NOW = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)


class FastEntryTests(unittest.TestCase):
    def test_existing_plans_take_priority_over_shortlist_score(self):
        summary = {"ranked_review_candidates": [
            {"instrument": "EUR_USD", "research_priority": 90},
            {"instrument": "GBP_JPY", "research_priority": 70, "paper_deep_scout_eligible": True},
            {"instrument": "AUD_USD", "research_priority": 99},
            {"instrument": "GBP_ZAR", "research_priority": 200},
        ]}
        chosen, omitted = fast.choose_pairs(summary,
            {"signals": [{"instrument": "USD_JPY"}]}, limit=3)
        self.assertEqual(chosen, ["USD_JPY", "GBP_JPY", "AUD_USD"])
        self.assertIn("EUR_USD", omitted)
        self.assertNotIn("GBP_ZAR", chosen + omitted)

    def test_missing_or_stale_hourly_source_fails_closed_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"data").mkdir()
            (root/"data/summary.json").write_text(json.dumps({
                "status": "OK",
                "collected_at": (NOW-timedelta(hours=2)).isoformat()}))
            with patch.object(fast, "ROOT", root), patch.object(
                fast, "FAST_AUDIT", root/"data/fast_entry_audit.json"), patch.object(
                fast.market, "request", side_effect=AssertionError("Network must not be called")):
                result = fast.perform(NOW)
                self.assertEqual(result["status"], "STALE_HOURLY_SOURCE")
                self.assertEqual(result["actionable_count"], 0)
                self.assertTrue((root/"data/fast_entry_audit.json").exists())

    def test_detected_trigger_is_not_described_as_delivered(self):
        plan = {"signals": [{
            "id": "X", "created_at": "2026-10-29T09:00:00Z",
            "planned_entry": 1.1, "stop": 1.099, "tp1": 1.102}]}
        assessment = {
            "collected_at": "2026-10-29T09:32:00Z",
            "oanda_collected_at": "2026-10-29T09:31:50Z",
            "signals": [{
                "id": "X", "pair": "EUR_USD", "direction": "LONG",
                "status": "PRICE_REASSESSMENT_ALERT_READY", "route": "C",
                "trigger_bar_open_time": "2026-10-29T09:15:00Z"}]}
        ledger = fast.record_events({}, assessment, plan)
        event = next(iter(ledger["events"].values()))
        self.assertEqual(event["trigger_closed_at"], "2026-10-29T09:30:00+00:00")
        self.assertEqual(event["first_detected_at"], "2026-10-29T09:32:00Z")
        self.assertIsNone(event["verified_user_delivery_at"])
        self.assertFalse(event["broker_fill_verified"])
        self.assertEqual(ledger, fast.record_events(ledger, assessment, plan))
        observation = fast.observation_rows(assessment, plan)[0]
        self.assertEqual(observation["entry"], 1.1)
        self.assertFalse(observation["user_delivery_verified"])
        self.assertTrue(observation["requires_manual_pepperstone_check"])

    def test_nontrigger_never_invents_entry_event(self):
        assessment = {"collected_at": NOW.isoformat(), "signals": [
            {"id": "X", "pair": "EUR_USD",
             "status": "NO_QUALIFIED_PRICE_PATTERN"}]}
        ledger = fast.record_events({}, assessment, {"signals": []})
        self.assertEqual(ledger["events"], {})
        self.assertIsNone(fast.observation_rows(assessment, {"signals": []})[0]["alert_detected_at"])

if __name__ == "__main__":
    unittest.main()

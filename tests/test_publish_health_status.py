import unittest
from pathlib import Path
from scripts import publish_health_status as h


class PublishHealthStatusTests(unittest.TestCase):
    def test_outage_invalidation_keeps_source_clock_and_original(self):
        old = {
            "snapshot_at": "2026-11-03T11:55:41Z",
            "published_at": "2026-11-03T14:54:00Z",
            "status": "OK",
            "signals": [{"id": "original", "entry": 1.1}],
            "hourly": {"source_at": "2026-11-03T11:55:41Z", "signals": [{"id": "t"}]},
            "learning": {"paper_audit": {"totals": {"candidates": 100}}},
        }
        out = h.unavailable_payload(old, "MARKET_COLLECTION_FAILED", "2026-11-04T09:59:00Z")
        self.assertEqual(out["assessment_status"], "ASSESSMENT_UNAVAILABLE")
        self.assertEqual(out["snapshot_at"], old["snapshot_at"])
        self.assertEqual(out["hourly"]["source_at"], old["hourly"]["source_at"])
        self.assertEqual(out["published_at"], "2026-11-04T09:59:00Z")
        self.assertEqual(out["signals"], [])
        self.assertEqual(out["hourly"]["signals"], [])
        self.assertEqual(out["learning"], old["learning"])
        self.assertEqual(old["signals"][0]["id"], "original")

    def test_no_unverifiable_old_feed_is_published(self):
        for value in ({}, {"snapshot_at": None}, []):
            with self.assertRaises(ValueError):
                h.unavailable_payload(value, "COLLECTOR", "2026-11-04T09:59:00Z")

    def test_data_collection_precedes_rule_safety_test(self):
        workflow=(Path(__file__).resolve().parents[1]/
                  ".github/workflows/oanda-hourly-research.yml").read_text()
        self.assertLess(workflow.index("name: Fetch OANDA raw data"),
                        workflow.index("name: Offline safety tests"))
        self.assertIn("id: safety",workflow)
        self.assertIn("FX_SAFETY_OUTCOME:",workflow)
        self.assertIn("scripts/publish_health_status.py",workflow)


if __name__ == "__main__":
    unittest.main()

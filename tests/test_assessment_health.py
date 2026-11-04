"""Operational acceptance: collection remains independent and invalid signals cannot leak."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from scripts.assessment_health import assess, apply_to_public_feed, ESSENTIAL

T = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


def stages(**overrides):
    return {**{key: "success" for key in ESSENTIAL}, **overrides}


def source(minutes=2):
    return {"status": "OK", "collected_at": (T-timedelta(minutes=minutes)).isoformat()}


class AssessmentHealthTests(unittest.TestCase):
    def test_successful_collection_and_validated_pipeline(self):
        row = assess(source(), stages(), now=T)
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertEqual(row["reason_codes"], [])
        self.assertFalse(row["user_delivery_verified"])

    def test_failed_integrity_blocks_approval_even_if_quotes_are_fresh(self):
        row = assess(source(), stages(safety="failure", admission="skipped"), now=T)
        self.assertEqual(row["status"], "ASSESSMENT_UNAVAILABLE")
        self.assertIn("SAFETY_FAILURE", row["reason_codes"])
        feed = {"status": "OK", "snapshot_at": source()["collected_at"],
                "signals": [{"id": "OLD_GREEN"}], "research_setups": [{"instrument": "EUR_USD"}],
                "hourly": {"signals": [{"status": "PRICE_REASSESSMENT_ALERT_READY"}]},
                "learning": {"current_scouts": [{"instrument": "EUR_USD"}], "entry_diagnostics": [{}]}}
        out = apply_to_public_feed(feed, row, expected_source_at=source()["collected_at"], now=T)
        self.assertEqual(out["status"], "ASSESSMENT_UNAVAILABLE")
        self.assertEqual(out["signals"], [])
        self.assertEqual(out["research_setups"], [])
        self.assertEqual(out["hourly"]["signals"], [])
        self.assertEqual(out["learning"]["current_scouts"], [])
        self.assertEqual(out["learning"]["assessment_health"]["status"], "ASSESSMENT_UNAVAILABLE")

    def test_missing_health_is_unavailable(self):
        feed = {"status": "OK", "snapshot_at": source()["collected_at"], "signals": [{"id": "X"}]}
        out = apply_to_public_feed(feed, None, expected_source_at=feed["snapshot_at"], now=T)
        self.assertEqual(out["status"], "ASSESSMENT_UNAVAILABLE")
        self.assertEqual(out["signals"], [])

    def test_mismatched_and_stale_sources_do_not_inherit_good_health(self):
        ok = assess(source(), stages(), now=T)
        for old in (source(72), source(-30)):
            feed = {"status": "OK", "snapshot_at": old["collected_at"], "signals": [{"id": "X"}]}
            out = apply_to_public_feed(feed, ok, expected_source_at=source()["collected_at"], now=T)
            self.assertEqual(out["status"], "ASSESSMENT_UNAVAILABLE")
        feed = {"status": "OK", "snapshot_at": source()["collected_at"], "signals": [{"id": "X"}]}
        self.assertEqual(apply_to_public_feed(feed, ok, expected_source_at=source(5)["collected_at"], now=T)["signals"], [])

    def test_unavailable_publication_is_idempotent_without_forged_market_clock(self):
        prior = source()["collected_at"]
        bad = assess(source(), stages(safety="failure"), now=T)
        feed = {"status": "OK", "snapshot_at": prior, "published_at": prior,
                "signals": [{"id": "X"}], "learning": {"paper_audit": {"totals": {"candidates": 1190}}}}
        first = apply_to_public_feed(feed, bad, expected_source_at=prior, now=T)
        second = apply_to_public_feed(first, bad, expected_source_at=prior, now=T)
        self.assertEqual(first, second)
        self.assertEqual(second["snapshot_at"], prior)
        self.assertEqual(second["published_at"], prior)
        self.assertEqual(second["learning"]["paper_audit"]["totals"]["candidates"], 1190)

    def test_source_collection_happens_before_fail_closed_tests(self):
        yml = (Path(__file__).resolve().parents[1] / ".github/workflows/oanda-hourly-research.yml").read_text()
        self.assertLess(yml.index("name: Fetch OANDA raw data"), yml.index("name: Offline safety tests"))
        self.assertIn("id: health", yml)
        self.assertIn("if: steps.health.outcome == 'success'", yml)
        self.assertIn("steps.safety.outcome == 'success' && steps.collect.outcome", yml)
        self.assertIn("steps.safety.outcome != 'success'", yml)


if __name__ == "__main__":
    unittest.main()

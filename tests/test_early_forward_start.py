"""Regression tests: independently frozen 4 November cohort only admits later observations."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import early_forward_start as early
from scripts import simple_paper_shadow_v2 as original


class EarlyForwardStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.simple_file = Path(self.temp.name) / "simple.json"
        self.report_file = Path(self.temp.name) / "report.json"
        self.p1 = patch.object(early, "EARLY_SIMPLE", self.simple_file)
        self.p2 = patch.object(early, "EARLY_REPORT", self.report_file)
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)

    def test_unchanged_original_frozen_protocols(self):
        p, digest = early.check_protocol(original.when("2026-11-04T14:31:00Z"))
        self.assertEqual(p["holdout_start_at"], "2026-11-04T14:30:00Z")
        self.assertEqual(len(digest), 64)
        self.assertEqual(original.START, "2026-11-05T07:00:00Z")

    def test_before_start_cannot_admit_snapshot(self):
        report, shadow = early.run(
            {"status": "OK", "collected_at": "2026-11-04T14:29:00Z",
             "pairs": {}, "ranked_review_candidates": []},
            {}, now="2026-11-04T14:29:10Z")
        self.assertEqual(shadow["status"], "HOLDOUT_NOT_STARTED")
        self.assertEqual(shadow["source_snapshots"], [])
        self.assertEqual(report["status"], "HOLDOUT_NOT_STARTED")
        self.assertEqual(report["arms"]["champion"]["prospective_plans"], 0)

    def test_after_start_records_new_source_without_backdating(self):
        report, shadow = early.run(
            {"status": "OK", "collected_at": "2026-11-04T14:30:20Z",
             "pairs": {}, "ranked_review_candidates": []},
            {}, now="2026-11-04T14:30:30Z")
        self.assertEqual(shadow["source_snapshots"], ["2026-11-04T14:30:20Z"])
        self.assertEqual(report["status"], "COLLECTING_HOLDOUT")
        self.assertEqual(shadow["metrics"]["plans"], 0)
        self.assertEqual(original.START, "2026-11-05T07:00:00Z")
        self.assertFalse(report["performance_validated"])

    def test_lock_conflict_refuses_overwrite(self):
        self.simple_file.write_text(json.dumps({
            "locked_protocol_sha256": "incorrect",
            "study_version": "simple-shadow-early-20261104-v1",
        }))
        with self.assertRaisesRegex(ValueError, "EARLY_COHORT_LOCK_CONFLICT"):
            early.run(
                {"status": "OK", "collected_at": "2026-11-04T14:31:00Z",
                 "pairs": {}, "ranked_review_candidates": []},
                {}, now="2026-11-04T14:32:00Z")
        self.assertEqual(json.loads(self.simple_file.read_text())[
            "locked_protocol_sha256"], "incorrect")


if __name__ == "__main__":
    unittest.main()

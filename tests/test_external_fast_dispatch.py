"""Static dispatch safety: external scheduler selects fast backup, not whole hourly collector.

The frozen fast scanner and registered research-rule hashes are NEVER edited.
"""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WORKFLOW=ROOT/".github/workflows/oanda-hourly-research.yml"
FAST_FROZEN=ROOT/".github/workflows/oanda-fast-entry.yml"


class ExternalFastDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=WORKFLOW.read_text()

    def test_explicit_named_choice_defaults_to_full(self):
        self.assertIn("  workflow_dispatch:\n    # A separate",self.config)
        self.assertIn("      monitoring_mode:",self.config)
        self.assertIn("        default: full",self.config)
        self.assertIn("          - fast",self.config)

    def test_fast_dispatch_cannot_invoke_full_snapshot_job(self):
        clause=next(x for x in self.config.splitlines()
                    if x.strip().startswith("if: (github.event_name != 'schedule'"))
        self.assertIn("github.event_name != 'workflow_dispatch'",clause)
        self.assertIn("inputs.monitoring_mode != 'fast'",clause)

    def test_dispatch_fast_is_same_existing_backup(self):
        clause=next(x for x in self.config.splitlines()
                    if x.strip().startswith("if: (github.event_name == 'schedule'"))
        self.assertIn("github.event.schedule == '4,19,34,49 7-22 * * 1-5'",clause)
        self.assertIn("github.event_name == 'workflow_dispatch'",clause)
        self.assertIn("inputs.monitoring_mode == 'fast'",clause)
        for name in ("python scripts/source_freshness_fallback.py",
                     "python scripts/monitoring_health.py --fast-if-due",
                     "python scripts/simple_shadow_gate_diagnostics.py --fast",
                     "python scripts/export_public_dashboard.py"):
            self.assertIn(name,self.config)

    def test_no_broker_or_external_credentials_embedded(self):
        self.assertNotIn("broker_order", self.config)
        self.assertNotIn("Bearer ghp_", self.config)
        self.assertNotIn("cron-job.org", self.config)
        self.assertIn("cancel-in-progress: false",self.config)

    def test_original_fast_workflow_unchanged_by_dispatch(self):
        self.assertIn("name: FX fast shortlisted entry research",
                      FAST_FROZEN.read_text())
        self.assertNotIn("monitoring_mode",FAST_FROZEN.read_text())


if __name__=="__main__":
    unittest.main()

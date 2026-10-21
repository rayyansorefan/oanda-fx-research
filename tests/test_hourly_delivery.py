"""Tests for truthful hourly acknowledgement and meaningful-trade delivery gating."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hourly_delivery as delivery

NOW = datetime(2026, 10, 21, 8, 27, tzinfo=timezone.utc)
STEPS = {"OANDA": "success", "news": "success",
         "setups": "success", "snapshot save": "success"}


def report(number=0):
    at = NOW.isoformat(timespec="seconds")
    return {"status": "OK", "collected_at": at, "oanda_collected_at": at,
            "news_collected_at": at, "news_coverage": "PARTIAL",
            "signals": [], "counts": {"watchlist": number,
                                      "need_news_review": 0, "price_alerts": 0}}


class DeliveryTests(unittest.TestCase):
    def test_schedule_only_not_push_or_early_backup(self):
        self.assertTrue(delivery.should_send("schedule", delivery.HOURLY_CRON))
        self.assertFalse(delivery.should_send("schedule", "5 6,7 * * 1-5"))
        self.assertFalse(delivery.should_send("push", delivery.HOURLY_CRON))
        self.assertTrue(delivery.should_send("workflow_dispatch", ""))

    def test_zero_setup_heartbeat_discloses_not_monitoring(self):
        label, message, status = delivery.evaluate(
            report(), {"calendar": {"status": "OK"}}, STEPS, NOW)
        self.assertEqual(label, "Update conducted")
        self.assertEqual(status, "HEARTBEAT")
        self.assertIn("Zero approved FX setups", message)
        self.assertIn("PARTIAL", message)

    def test_one_approved_idea_without_change(self):
        item = report(1)
        item["signals"] = [{"pair": "GBP_USD", "status": "NO_QUALIFIED_PRICE_PATTERN"}]
        label, message, status = delivery.evaluate(
            item, {"calendar": {"status": "OK"}}, STEPS, NOW)
        self.assertEqual(label, "Update conducted")
        self.assertIn("1 approved FX setup(s) checked", message)

    def test_existing_important_issue_does_not_create_duplicate(self):
        item = report(1)
        item["signals"] = [{"pair": "GBP_USD", "status": "PRICE_REASSESSMENT_ALERT_SENT"}]
        label, message, status = delivery.evaluate(item, {}, STEPS, NOW)
        self.assertEqual(status, "ALREADY_NOTIFIED")
        self.assertEqual(label, "Action needed")

    def test_news_review_already_sent(self):
        item = report(1)
        item["signals"] = [{"pair": "GBP_USD", "status": "REASSESS_NEWS_FIRST"}]
        self.assertEqual(delivery.evaluate(item, {}, STEPS, NOW)[2], "ALREADY_NOTIFIED")

    def test_breached_trade_needs_attention(self):
        item = report(1)
        item["signals"] = [{"pair": "EUR_USD",
                            "status": "REASSESS_OR_EXPIRE_PRIOR_PLAN_LEVEL_REACHED"}]
        self.assertEqual(delivery.evaluate(item, {}, STEPS, NOW)[0], "Action needed")

    def test_unverified_news_blocks_generic_success(self):
        item = report(1)
        item["signals"] = [{"pair": "USD_JPY", "status": "NEWS_COVERAGE_NOT_VERIFIED"}]
        self.assertEqual(delivery.evaluate(item, {}, STEPS, NOW)[0], "Action needed")

    def test_collect_or_snapshot_persist_failure(self):
        for failure in ("OANDA", "news", "setups", "snapshot save"):
            steps = dict(STEPS)
            steps[failure] = "failure"
            label, message, status = delivery.evaluate(report(), {}, steps, NOW)
            self.assertEqual(label, "Update failed")
            self.assertIn(failure, message)
            self.assertEqual(status, "FAILURE")

    def test_stale_news_not_success(self):
        bad = report()
        bad["news_collected_at"] = (NOW - timedelta(minutes=90)).isoformat()
        self.assertEqual(delivery.evaluate(bad, {}, STEPS, NOW)[0], "Update failed")

    def test_timestamps_cannot_be_in_future(self):
        bad = report()
        bad["oanda_collected_at"] = (NOW + timedelta(hours=1)).isoformat()
        self.assertEqual(delivery.evaluate(bad, {}, STEPS, NOW)[0], "Update failed")

    def test_integration_creates_issue_for_completed_scheduled_only(self):
        with tempfile.TemporaryDirectory() as temp:
            hourly = Path(temp) / "assessment.json"
            news = Path(temp) / "news.json"
            hourly.write_text(json.dumps(report()))
            news.write_text(json.dumps({"calendar": {"status": "OK"}}))
            mocked = []
            env = {"GITHUB_EVENT_NAME": "schedule", "GITHUB_SCHEDULE": delivery.HOURLY_CRON,
                   "GITHUB_REPOSITORY": "example/fx-research", "GITHUB_TOKEN": "dummy",
                   "GITHUB_RUN_ID": "123",
                   "FX_COLLECT_OUTCOME": "success", "FX_NEWS_OUTCOME": "success",
                   "FX_WATCH_OUTCOME": "success", "FX_PERSIST_OUTCOME": "success"}
            with patch.object(delivery, "ASSESSMENT", hourly), \
                 patch.object(delivery, "NEWS", news), \
                 patch.object(delivery, "create_issue", side_effect=lambda title, body, repo, tok:
                             mocked.append((title, body, repo)) or "https://example.org/issue"), \
                 patch.dict(delivery.os.environ, env), \
                 patch.object(delivery, "datetime", wraps=datetime):
                # Current timestamp is generated by datetime.now(); regenerate
                # a fresh report so the freshness gate still holds.
                fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
                data = report()
                for key in ("collected_at", "oanda_collected_at", "news_collected_at"):
                    data[key] = fresh
                hourly.write_text(json.dumps(data))
                delivery.run()
            self.assertEqual(len(mocked), 1)
            self.assertIn("Update conducted", mocked[0][0])
            self.assertIn("Zero approved FX setups", mocked[0][1])
            self.assertIn("actions/runs/123", mocked[0][1])


if __name__ == "__main__":
    unittest.main()

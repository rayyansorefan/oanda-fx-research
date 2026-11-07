"""Independent prospective paper-only liquid momentum acceptance tests."""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts import liquid_momentum_shadow_v1 as study


NOW = datetime(2026, 11, 9, 8, 40, tzinfo=timezone.utc)


def frame(when, indicators, bars=None):
    return {"completed_at": when, "indicators": indicators,
            "last_3_completed_candles": bars or []}


def fixture():
    bars = [
        {"time": "2026-11-09T07:45:00Z",
         "mid": {"o": 1.1003, "h": 1.1006, "l": 1.1001, "c": 1.1004}},
        {"time": "2026-11-09T08:00:00Z",
         "mid": {"o": 1.1004, "h": 1.1008, "l": 1.1000, "c": 1.1005}},
        {"time": "2026-11-09T08:15:00Z",
         "mid": {"o": 1.1002, "h": 1.1012, "l": 1.1001, "c": 1.1011}},
    ]
    pair = {
        "quote": {"time": "2026-11-09T08:39:30Z", "bid": 1.1011,
                  "ask": 1.1012, "pip_size": 0.0001,
                  "tradeable": True},
        "frames": {
            "M15": frame("2026-11-09T08:15:00Z",
                         {"ema20": 1.10025, "ema50": 1.0998,
                          "atr14": 0.0008}, bars),
            "H1": frame("2026-11-09T07:00:00Z",
                        {"ema20": 1.0999, "ema50": 1.0993, "adx14": 24}),
        },
    }
    summary = {"status": "OK", "collected_at": "2026-11-09T08:39:20Z",
               "pairs": {"EUR_USD": pair}}
    news = {"collected_at": "2026-11-09T08:39:30Z", "by_pair": {}}
    return summary, news


class LiquidMomentumTests(unittest.TestCase):
    def setUp(self):
        self.summary, self.news = fixture()

    def plans(self, *args, **kwargs):
        with patch.object(study.contextual, "contextual_news_gate", return_value=None), \
             patch.object(study.contextual, "news_uncertainty", return_value=[]):
            return study.propose(*args, **kwargs)

    def test_frozen_protocol_validates(self):
        p = study.validate_protocol()
        self.assertEqual(p["study_version"], study.VERSION)
        self.assertFalse(p["validation"]["automatic_live_promotion"])

    def test_future_confirmed_impulse_emits_paper_plan_not_live_signal(self):
        plans = self.plans(self.summary, self.news, NOW)
        self.assertEqual(len(plans), 1)
        p = plans[0]
        self.assertEqual(p["instrument"], "EUR_USD")
        self.assertEqual(p["direction"], "LONG")
        self.assertTrue(p["experimental_paper_only"])
        self.assertFalse(p["approved_entry"])
        self.assertFalse(p["broker_order_permitted"])
        self.assertGreaterEqual(p["estimated_net_rr_before_slippage"], 1.35)
        self.assertGreater(study.base.when(p["expires_at"]), NOW)
        self.assertGreater(p["trigger"], p["indicative_entry"])

    def test_stale_quote_cannot_generate_plan(self):
        self.summary["pairs"]["EUR_USD"]["quote"]["time"] = "2026-11-09T08:10:00Z"
        self.assertEqual(self.plans(self.summary, self.news, NOW), [])

    def test_spread_is_not_ignored(self):
        self.summary["pairs"]["EUR_USD"]["quote"]["ask"] = 1.10155
        self.assertEqual(self.plans(self.summary, self.news, NOW), [])

    def test_unconfirmed_direction_is_not_a_trade(self):
        self.summary["pairs"]["EUR_USD"]["frames"]["H1"]["indicators"]["ema20"] = 1.0990
        self.assertEqual(self.plans(self.summary, self.news, NOW), [])

    def test_known_news_blackout_still_blocks(self):
        with patch.object(study.contextual, "contextual_news_gate",
                          return_value="CONTEXT_CALENDAR_UNAVAILABLE_OR_BLACKOUT"):
            self.assertEqual(study.propose(self.summary, self.news, NOW), [])

    def test_no_registration_before_registered_forward_start(self):
        earlier = datetime(2026, 11, 8, 8, 40, tzinfo=timezone.utc)
        self.assertEqual(self.plans(self.summary, self.news, earlier), [])

    def test_same_source_does_not_create_duplicate_paper_entries(self):
        with patch.object(study.contextual, "contextual_news_gate", return_value=None), \
             patch.object(study.contextual, "news_uncertainty", return_value=[]):
            book = study.update(self.summary, self.news, fetch=lambda plan, now: [], now=NOW)
            self.assertEqual(book["metrics"]["plans"], 1)
            self.assertEqual(book["metrics"]["filled"], 0)
            self.assertFalse(book["automatic_promotion_allowed"])
            rerun = study.update(self.summary, self.news, book,
                                 fetch=lambda plan, now: [], now=NOW)
        self.assertEqual(rerun["metrics"]["plans"], 1)
        self.assertEqual(book["rule_sha256"], study.digest())

    def test_rule_change_cannot_rewrite_existing_evidence(self):
        with self.assertRaisesRegex(ValueError, "FROZEN_RULES_CHANGED"):
            study.update(self.summary, self.news, {"rule_sha256": "modified"},
                         fetch=lambda plan, now: [], now=NOW)


if __name__ == "__main__":
    unittest.main()

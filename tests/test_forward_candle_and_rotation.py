"""Reproducible forward-only timing and fair bounded fast-coverage regressions."""
import unittest
from datetime import datetime, timezone
from scripts import fast_entry_scan as fast
from scripts.signal_watch import signal_match, timely_completed_candle
from test_m5_continuation import market, plan


class ForwardTimingAndRotationTests(unittest.TestCase):
    def test_late_m5_breakout_never_turns_green_after_price_moved(self):
        snapshot = market()
        snapshot["quote"]["time"] = "2026-11-02T10:17:30Z"
        state = {}
        current = datetime(2026, 11, 2, 10, 18, tzinfo=timezone.utc)
        self.assertIsNone(signal_match(plan(), snapshot, state, current))
        self.assertEqual(state["late_candles_skipped"], 1)
        self.assertEqual(state["last_late_candle_close"],
                         "2026-11-02T10:10:00+00:00")
        self.assertEqual(state["last_processed_bar_close"],
                         "2026-11-02T10:10:00+00:00")
        # Repeating the same snapshot cannot reprocess the same old candle.
        self.assertIsNone(signal_match(plan(), snapshot, state, current))
        self.assertEqual(state["late_candles_skipped"], 1)

    def test_timeliness_never_accepts_future_candle_or_hour_old_pattern(self):
        now = datetime(2026, 11, 2, 10, 20, tzinfo=timezone.utc)
        self.assertTrue(timely_completed_candle(
            datetime(2026, 11, 2, 10, 16, tzinfo=timezone.utc), now, "M5"))
        self.assertFalse(timely_completed_candle(
            datetime(2026, 11, 2, 10, 14, tzinfo=timezone.utc), now, "M5"))
        self.assertFalse(timely_completed_candle(
            datetime(2026, 11, 2, 10, 21, tzinfo=timezone.utc), now, "M15"))
        self.assertFalse(timely_completed_candle(
            datetime(2026, 11, 2, 9, 20, tzinfo=timezone.utc), now, "M15"))
        self.assertTrue(timely_completed_candle(
            datetime(2026, 11, 2, 10, 5, tzinfo=timezone.utc), now, "M15"))

    def test_stable_rank_never_starves_unobserved_pairs_in_fast_scans(self):
        names = ["FX_%02d" % i for i in range(67)]
        summary = {"ranked_review_candidates": [
            {"instrument": p, "research_priority": 100-i,
             "deep_entry_scout_eligible": i < 3}
            for i, p in enumerate(names)]}
        cursor = 0
        observed = set()
        for _ in range(13):
            chosen, omitted, advanced = fast.choose_pairs(
                summary, {"signals": []}, limit=8, cursor=cursor, with_rotation=True)
            self.assertEqual(chosen[:3], names[:3])
            self.assertEqual(len(chosen), 8)
            self.assertEqual(len(omitted), 59)
            observed.update(chosen)
            cursor += advanced
        self.assertEqual(len(observed), 67)

    def test_frozen_plan_wins_even_when_not_in_hourly_ranking(self):
        summary = {"ranked_review_candidates": [
            {"instrument": "EUR_USD", "research_priority": 99},
            {"instrument": "GBP_USD", "research_priority": 98},
            {"instrument": "AUD_USD", "research_priority": 97},
            {"instrument": "USD_JPY", "research_priority": 96},
        ]}
        selected, _, steps = fast.choose_pairs(
            summary, {"signals": [{"instrument": "NZD_JPY"}]},
            limit=3, cursor=3, with_rotation=True)
        self.assertEqual(selected[0], "NZD_JPY")
        self.assertEqual(len(selected), 3)
        self.assertEqual(steps, 2)


if __name__ == "__main__":
    unittest.main()

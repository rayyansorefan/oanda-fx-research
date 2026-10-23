"""No-network regression for momentum-continuation alert C."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import signal_watch as watch

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def sample(long=True):
    if long:
        entry, stop, tp, level, bid, ask = (1.3378, 1.3358, 1.3428,
                                            1.3378, 1.3379, 1.3380)
        previous, bar = 1.3372, {"o": 1.3371, "h": 1.3384,
                                  "l": 1.3370, "c": 1.3383}
    else:
        entry, stop, tp, level, bid, ask = (1.3378, 1.3398, 1.3328,
                                            1.3378, 1.3376, 1.3377)
        previous, bar = 1.3384, {"o": 1.3384, "h": 1.3385,
                                  "l": 1.3372, "c": 1.3373}
    def ind():
        return {"adx14": 27, "plus_di14": 30 if long else 12,
                "minus_di14": 12 if long else 30}
    pair = {
        "quote": {"bid": bid, "ask": ask, "spread_pips": 1.0,
                  "pip_size": .0001, "tradeable": True, "time": NOW.isoformat()},
        "frames": {
            "M15": {
                "indicators": ind(),
                "recent_completed_candles": [
                    {"time": (NOW - timedelta(minutes=30)).isoformat(),
                     "mid": {"o": previous, "h": previous+.0001,
                             "l": previous-.0001, "c": previous}},
                    {"time": (NOW - timedelta(minutes=15)).isoformat(),
                     "mid": bar}
                ]},
            "H1": {"indicators": ind()}
        }
    }
    plan = {
        "id": "TEST-C-01", "instrument": "GBP_USD",
        "direction": "LONG" if long else "SHORT",
        "created_at": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(hours=3)).isoformat(),
        "planned_entry": entry, "stop": stop, "tp1": tp,
        "alert_c": level, "max_oanda_spread_pips": 1.3,
        "event_blackouts_utc": []
    }
    return pair, plan


class ContinuationTests(unittest.TestCase):
    def test_long_continuation_without_later_retest(self):
        pair, plan = sample(True)
        self.assertIsNone(watch.validate_approved_plan(plan, pair, NOW))
        found = watch.signal_match(plan, pair, {}, NOW)
        self.assertEqual(found[0], "C")

    def test_short_continuation_without_later_retest(self):
        pair, plan = sample(False)
        self.assertIsNone(watch.validate_approved_plan(plan, pair, NOW))
        found = watch.signal_match(plan, pair, {}, NOW)
        self.assertEqual(found[0], "C")

    def test_absent_h1_trend_confirmation_is_no_alert(self):
        pair, plan = sample(True)
        pair["frames"]["H1"]["indicators"]["adx14"] = 12
        self.assertIsNone(watch.signal_match(plan, pair, {}, NOW))

    def test_faded_quote_below_breakout_is_no_alert(self):
        pair, plan = sample(True)
        pair["quote"]["bid"], pair["quote"]["ask"] = 1.3375, 1.3376
        self.assertIsNone(watch.signal_match(plan, pair, {}, NOW))

    def test_stale_market_quote_is_no_alert(self):
        pair, plan = sample(True)
        pair["quote"]["time"] = (NOW - timedelta(minutes=8)).isoformat()
        self.assertIsNone(watch.signal_match(plan, pair, {}, NOW))


if __name__ == "__main__":
    unittest.main()

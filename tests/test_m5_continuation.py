"""M5 prospective continuation regression: no retroactive or unsafe alerts."""
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from scripts.signal_watch import signal_match, setup_already_breached
from scripts.fast_entry_scan import record_events

def plan():
    return {
        "id": "M5-FUTURE-1", "instrument": "EUR_USD", "direction": "LONG",
        "entry_route": "MOMENTUM_CONTINUATION", "trigger_timeframe": "M5",
        "created_at": "2026-11-02T10:05:00Z",
        "expires_at": "2026-11-02T11:35:00Z",
        "alert_c": 1.1007, "planned_entry": 1.1009,
        "stop": 1.0990, "tp1": 1.1050,
        "max_oanda_spread_pips": 2.0,
        "technical_context": {
            "admission_policy": "champion", "trigger_timeframe": "M5",
            "continuation_min_body": .50, "continuation_min_close": .70,
            "momentum_adx_minimum": 20,
            "m15_adx": 28, "h1_adx": 28,
            "m15_plus_di": 30, "m15_minus_di": 12,
            "h1_plus_di": 30, "h1_minus_di": 12,
        }
    }

def bars():
    return [
        {"time":"2026-11-02T10:00:00Z",
         "mid":{"o":1.1002, "h":1.10064, "l":1.1001, "c":1.1005},
         "bid":{"h":1.1006,"l":1.10005},
         "ask":{"h":1.1007,"l":1.10015}},
        {"time":"2026-11-02T10:05:00Z",
         "mid":{"o":1.1005,"h":1.10110,"l":1.10046,"c":1.10104},
         "bid":{"h":1.10105,"l":1.10041},
         "ask":{"h":1.10115,"l":1.10051}},
    ]

def market():
    ind = {"ema20":1.1000,"ema50":1.0994,"adx14":28,
           "plus_di14":30,"minus_di14":12}
    return {"quote":{"time":"2026-11-02T10:10:30Z", "tradeable":True,
                    "bid":1.10100,"ask":1.10110, "spread_pips":1.0,
                    "pip_size":.0001},
            "frames":{"M5":{"recent_completed_candles":bars()},
                      "M15":{"indicators":ind,
                             "recent_completed_candles":bars()},
                      "H1":{"indicators":dict(ind)},
                      "H4":{"indicators":dict(ind)},
                      "D":{"indicators":dict(ind)}}}

class M5ContinuationTests(unittest.TestCase):
    def test_future_completed_m5_can_trigger_without_next_m15(self):
        found = signal_match(plan(), market(), {},
                             datetime(2026,11,2,10,11,tzinfo=timezone.utc))
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "C")
        self.assertEqual(found[1], "2026-11-02T10:05:00Z")
        self.assertIn("M5", found[2])

    def test_incomplete_m5_does_not_trigger(self):
        m = market()
        m["quote"]["time"] = "2026-11-02T10:08:30Z"
        self.assertIsNone(signal_match(
            plan(), m, {}, datetime(2026,11,2,10,9,tzinfo=timezone.utc)))

    def test_old_signal_cannot_claim_precreation_breakout(self):
        p = plan()
        p["created_at"] = "2026-11-02T10:10:00Z"
        self.assertIsNone(signal_match(
            p, market(), {}, datetime(2026,11,2,10,11,tzinfo=timezone.utc)))

    def test_current_m15_dmi_or_trend_conflict_blocks_new_m5(self):
        for change in ({"ema20":1.0990}, {"plus_di14":9}):
            m = market()
            m["frames"]["M15"]["indicators"].update(change)
            self.assertIsNone(signal_match(
                plan(), m, {}, datetime(2026,11,2,10,11,tzinfo=timezone.utc)))

    def test_retroactively_touched_stop_or_target_prevents_alert(self):
        m = market()
        m["frames"]["M5"]["recent_completed_candles"][-1]["bid"]["l"] = 1.0980
        self.assertEqual(setup_already_breached(
            m, plan(), datetime(2026,11,2,10,11,tzinfo=timezone.utc)),
            "PLAN_STOP_LEVEL_TOUCHED_ON_M5")

    def test_missing_m5_executable_candle_fails_closed(self):
        m = market()
        m["frames"]["M5"]["recent_completed_candles"][-1].pop("bid")
        self.assertEqual(setup_already_breached(
            m, plan(), datetime(2026,11,2,10,11,tzinfo=timezone.utc)),
            "PLAN_EXECUTABLE_SIDE_HISTORY_MISSING")

    def test_trigger_close_clock_and_no_delivery_invention(self):
        p = plan()
        assessment = {"collected_at":"2026-11-02T10:11:00Z",
            "oanda_collected_at":"2026-11-02T10:10:30Z",
            "signals":[{"id":p["id"], "pair":"EUR_USD","direction":"LONG",
                        "status":"PRICE_REASSESSMENT_ALERT_READY","route":"C",
                        "trigger_bar_open_time":"2026-11-02T10:05:00Z"}]}
        row = next(iter(record_events({}, assessment, {"signals":[p]})["events"].values()))
        self.assertEqual(row["trigger_closed_at"],"2026-11-02T10:10:00+00:00")
        self.assertEqual(row["trigger_timeframe"],"M5")
        self.assertIsNone(row["verified_user_delivery_at"])
        self.assertFalse(row["broker_fill_verified"])

if __name__ == "__main__":
    unittest.main()

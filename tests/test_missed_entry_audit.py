"""Offline proof that missed-entry counts are not retrospective price gains."""
import unittest
import datetime as dt
from scripts.missed_entry_audit import outcome, audit

UTC=dt.timezone.utc
NOW=dt.datetime(2026,10,28,12,0,tzinfo=UTC)

def candle(time, o, high, low, close, spread=.00010):
    def p(delta):
        return {"o":o+delta, "h":high+delta, "l":low+delta,
                "c":close+delta}
    return {"t":dt.datetime.fromisoformat(time.replace("Z","+00:00")),
            "bid":p(0), "ask":p(spread)}

def idea():
    return {"id":"202610281000-EURUSD-LONG-EMA20_PULLBACK",
            "instrument":"EUR_USD","direction":"LONG","route":"EMA20_PULLBACK",
            "first_seen_at":"2026-10-28T10:02:00Z",
            "entry_pattern_at":"2026-10-28T10:45:00Z",
            "entry_pattern_status":"FORWARD_CANDLE_OBSERVED_PAPER_ONLY",
            "expires_at":"2026-10-28T11:15:00Z",
            "entry_low":1.1008,"entry_high":1.1010,
            "stop":1.0990,"tp1":1.1030}

def bars():
    return [
        candle("2026-10-28T10:15:00Z",1.1000,1.1005,1.0996,1.1004),
        candle("2026-10-28T10:30:00Z",1.1004,1.1012,1.0998,1.1010),
        candle("2026-10-28T10:45:00Z",1.1009,1.1012,1.1007,1.1011),
        candle("2026-10-28T11:00:00Z",1.1011,1.1032,1.1009,1.1031),
    ]

class AuditTests(unittest.TestCase):
    def test_prospective_limit_fill_then_later_take_profit(self):
        result=outcome(idea(),bars(),NOW)
        self.assertEqual(result["status"],"PAPER_TP1_AFTER_FILL")
        self.assertTrue(result["scored"])
        self.assertEqual(result["fill_at"],"2026-10-28T10:45:00Z")
        self.assertEqual(result["exit_at"],"2026-10-28T11:00:00Z")
        self.assertFalse(result["broker_verified"])
        self.assertFalse(result["trade_executed"])

    def test_prior_stop_cancels_even_if_later_target_hit(self):
        b=bars()
        b[0]=candle("2026-10-28T10:15:00Z",1.1000,1.1005,1.0989,1.1004)
        x=outcome(idea(),b,NOW)
        self.assertEqual(x["status"],"PAPER_PRE_ENTRY_INVALIDATED")
        self.assertFalse(x["scored"])

    def test_missing_pre_pattern_bars_are_not_counted_as_fills(self):
        b=bars()
        b.pop(1)
        result=outcome(idea(),b,NOW)
        self.assertEqual(result["status"],"PAPER_NO_CONTIGUOUS_CANDLES")
        self.assertFalse(result["scored"])

    def test_fill_and_target_in_same_candle_is_ambiguous(self):
        b=bars()
        b[2]=candle("2026-10-28T10:45:00Z",1.1009,1.1031,1.1007,1.1011)
        x=outcome(idea(),b,NOW)
        self.assertEqual(x["status"],"PAPER_AMBIGUOUS_FILL_BAR")
        self.assertFalse(x["scored"])

    def test_no_limit_touch_means_no_fill_even_with_pattern(self):
        b=bars()
        b[2]=candle("2026-10-28T10:45:00Z",1.1014,1.1017,1.1012,1.1016)
        b[3]=candle("2026-10-28T11:00:00Z",1.1016,1.1019,1.1012,1.1018)
        x=outcome(idea(),b,NOW)
        self.assertEqual(x["status"],"PAPER_NOT_FILLED_BEFORE_EXPIRY")
        self.assertFalse(x["scored"])

    def test_summary_deduplicates_pair_pattern_event_but_not_frozen_candidates(self):
        source=idea()
        other=dict(source)
        other["id"]="ANOTHER-FROZEN-PLAN"
        other["first_seen_at"]="2026-10-28T10:16:00Z"
        other["entry_pattern_at"]="2026-10-28T11:00:00Z"
        # Not valid until a pattern is later than the recorded bar; no result fabricated.
        group={"opportunities":{source["id"]:source,other["id"]:other}}
        report=audit(group,now=NOW,fetch=lambda row,now:bars())
        self.assertEqual(report["today_registered"],2)
        self.assertEqual(report["today_forward_patterns"],2)
        self.assertEqual(report["today_distinct_pair_pattern_events"],2)
        self.assertLessEqual(report["today_scored_hypothetical_entries"],2)

if __name__=="__main__":
    unittest.main()

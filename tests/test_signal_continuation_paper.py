"""Prospective continuation paper replay checks: no hindsight entry or fabricated wins."""
import unittest
import datetime as dt
from scripts import paper_trade_audit as audit

UTC=dt.timezone.utc
NOW=dt.datetime(2026,10,28,11,0,tzinfo=UTC)

def candle(t,o,h,l,c):
    spread=.00010
    return {"t":dt.datetime.fromisoformat(t.replace("Z","+00:00")),
            "mid":{"o":o+spread/2,"h":h+spread/2,
                   "l":l+spread/2,"c":c+spread/2},
            "bid":{"o":o,"h":h,"l":l,"c":c},
            "ask":{"o":o+spread,"h":h+spread,
                   "l":l+spread,"c":c+spread}}

def plan():
    return {"id":"TEST-AUTO-C-01","instrument":"EUR_USD","direction":"LONG",
            "created_at":"2026-10-28T10:05:00Z",
            "expires_at":"2026-10-28T11:59:00Z",
            "planned_entry":1.1012,"stop":1.0998,"tp1":1.1042,
            "max_oanda_spread_pips":1.5,
            "event_blackouts_utc":[],"alert_c":1.101,
            "technical_context":{
              "full_frame_alignment":True,"m15_adx":29,"h1_adx":31,
              "m15_plus_di":30,"m15_minus_di":10,
              "h1_plus_di":29,"h1_minus_di":11,"m15_atr":.0008}}

def candles():
    return [
        candle("2026-10-28T09:45:00Z",1.1004,1.1007,1.1002,1.10055),
        candle("2026-10-28T10:15:00Z",1.10075,1.10172,1.1007,1.10162),
        candle("2026-10-28T10:30:00Z",1.10125,1.1016,1.10110,1.1014),
        candle("2026-10-28T10:45:00Z",1.1014,1.1043,1.10135,1.10422),
    ]

class ContinuationReplayTests(unittest.TestCase):
    def test_continuation_needs_frozen_frame_proof(self):
        p=plan();p.pop("technical_context")
        self.assertEqual(audit.validate(p),"CONTINUATION_ORIGINAL_FRAME_EVIDENCE_MISSING")

    def test_later_completed_trigger_and_next_bar_open_are_evaluated(self):
        p=plan()
        result=audit.replay(p,candles(),NOW)
        self.assertEqual(result["status"],"WIN_TP1")
        self.assertTrue(result["scored"])
        self.assertEqual(result["route"],"C")
        self.assertEqual(result["entry_at"],"2026-10-28T10:30:00Z")
        self.assertAlmostEqual(result["entry"],1.10135)
        self.assertGreater(result["net_r"],1.35)

    def test_no_pre_signal_candle_can_be_triggered(self):
        p=plan()
        p["created_at"]="2026-10-28T10:31:00Z"
        result=audit.replay(p,candles(),NOW)
        self.assertNotEqual(result["status"],"WIN_TP1")

    def test_stop_or_target_inside_entry_bar_is_ambiguous(self):
        bars=candles()
        bars[2]=candle("2026-10-28T10:30:00Z",1.10125,1.1043,1.0997,1.1014)
        result=audit.replay(plan(),bars,NOW)
        self.assertEqual(result["status"],"AMBIGUOUS_ENTRY_BAR")
        self.assertFalse(result["scored"])

    def test_gap_or_bad_next_open_is_not_a_fill(self):
        bars=candles()
        bars[2]=candle("2026-10-28T10:30:00Z",1.1022,1.1026,1.102,1.1023)
        result=audit.replay(plan(),bars,NOW)
        self.assertEqual(result["status"],"INVALIDATED_PRE_ENTRY")
        self.assertFalse(result["scored"])

if __name__=="__main__":
    unittest.main()

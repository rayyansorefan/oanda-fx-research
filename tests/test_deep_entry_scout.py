"""Offline regression: deep research discovers trade-ready foundations before ranking noise."""
import unittest
from scripts.fx_universe import deep_selection, screen_priority
from scripts.signal_admission import qualify
from tests.test_signal_admission import source, news, ranked, NOW

class DeepScoutTests(unittest.TestCase):
    def test_deep_selection_prefers_verified_foundation_even_with_lower_rank(self):
        rankings={f"A{i:02d}_XYZ":{"research_priority":95-i}
                  for i in range(20)}
        rankings["AUD_HKD"]={"research_priority":31,"deep_entry_scout_eligible":True}
        picked=deep_selection(rankings,max_extra=12)
        self.assertIn("AUD_HKD",picked)
        self.assertLessEqual(len(picked),12)
        self.assertNotIn("A11_XYZ",picked)

    def test_screen_sets_scout_only_when_direction_cost_and_strength_agree(self):
        q={"tradeable":True,"bid":1.1000,"ask":1.1001,
           "pip_size":.0001,"spread_pips":1}
        def f():
            return {"indicators":{
                "ema20":1.101,"ema50":1.100,"ema200":1.098,
                "adx14":28,"plus_di14":30,"minus_di14":12,
                "atr14":.0008,"rsi14":55,
                "macd_12_26_9":{"histogram":.0001}}}
        rank=screen_priority(q,f(),f())
        self.assertTrue(rank["deep_entry_scout_eligible"])
        self.assertEqual(rank["deep_entry_scout_direction"],"LONG")
        self.assertTrue(rank["screening_only"])
        bad=f()
        bad["indicators"]["plus_di14"]=8
        bad["indicators"]["minus_di14"]=24
        self.assertFalse(screen_priority(q,bad,f())["deep_entry_scout_eligible"])

    def test_research_rank_bias_does_not_override_completed_ema_alignment(self):
        r=ranked()
        r["potential_direction_not_approved"]="SHORT"
        r["deep_entry_scout_direction"]="LONG"
        plan,reason=qualify("EUR_USD",r,source(),news(),NOW,"EMA20_PULLBACK")
        self.assertIsNone(reason)
        self.assertEqual(plan["direction"],"LONG")
        self.assertFalse(plan["broker_verified"])

if __name__=="__main__":
    unittest.main()

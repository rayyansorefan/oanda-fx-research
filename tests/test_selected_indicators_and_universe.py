"""Synthetic no-network regression of selected technical methods and 65-pair chart access."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from advanced_technicals import donchian, ichimoku, supertrend
from fx_universe import screen_priority, deep_selection
from export_public_dashboard import build_public_feed


def bars(count=230):
    out=[]
    for i in range(count):
        value=1.10 + i*0.00003
        out.append({"time":"2026-10-21T00:00:00Z",
                    "mid":{"o":value-0.0001,"h":value+0.0002,
                           "l":value-0.0002,"c":value}})
    return out

class TenMethodTests(unittest.TestCase):
    def test_donchian_uses_prior_completed_bars_only(self):
        rows=bars()
        previous=donchian(rows)
        rows[-1]["mid"]["c"]=rows[-1]["mid"]["h"]=previous["upper"]+.001
        row=donchian(rows)
        self.assertEqual(previous["upper"],row["upper"])
        self.assertEqual(row["close_breakout"],"UP")

    def test_ichimoku_visible_cloud_is_displaced(self):
        rows=bars()
        original=ichimoku(rows)
        self.assertEqual(original["forward_shift_bars"],26)
        rows[-1]["mid"]["h"]+=0.012
        rows[-1]["mid"]["c"]+=0.012
        newer=ichimoku(rows)
        self.assertEqual(original["cloud_at_current_a"],newer["cloud_at_current_a"])
        self.assertEqual(original["cloud_at_current_b"],newer["cloud_at_current_b"])
        self.assertNotEqual(original["conversion9"],newer["conversion9"])

    def test_supertrend_reacts_without_future_candles(self):
        rows=bars()
        first=supertrend(rows)
        self.assertIn(first["direction"],("UP","DOWN"))
        self.assertTrue(first["line"] is not None)
        self.assertGreater(first["line"],0)
        self.assertEqual(first,supertrend(rows))

    def test_all_instruments_screenable_even_spread_high(self):
        base={"tradeable":True,"bid":1.1000,"ask":1.1001,
              "spread_pips":20,"pip_size":0.0001}
        technicals={"ema20":1.1,"ema50":1.099,"rsi14":55,
                    "adx14":25,"plus_di14":27,"minus_di14":17,
                    "atr14":.0007,"macd_12_26_9":{"histogram":.0001}}
        trend={"advanced":{"ichimoku_9_26_52":{"price_vs_visible_cloud":"ABOVE"},
                           "supertrend_10_3":{"direction":"UP"},
                           "donchian_20":{"close_breakout":"UP"},
                           "bollinger_20_2":{"bandwidth_percentile_recent":32},
                           "stochastic_14_3":{"k":60}},
               "indicators":technicals}
        report=screen_priority(base,trend,trend)
        self.assertIsNotNone(report)
        self.assertTrue(report["high_relative_spread"])
        self.assertEqual(len(report["methods_reviewed"]),10)
        self.assertTrue(report["screening_only"])
        self.assertFalse("trade_signal" in report)

    def test_twelve_candidates_selected_not_only_two(self):
        ranking={"P%02d_Q%02d"%(i,i):{"research_priority":100-i}
                 for i in range(25)}
        picked=deep_selection(ranking,max_extra=12)
        self.assertEqual(len(picked),12)

    def test_non_finalist_candles_are_chartable_and_no_private_data(self):
        summary={"collected_at":"2026-10-21T19:00:00Z",
                 "coverage":{"oanda_account_fx_instruments_discovered":1,
                             "oanda_pairs_with_usable_live_quotes":1,
                             "oanda_pairs_with_m15_h1_indicators":1,
                             "detailed_audit_pairs":[]},
                 "pairs":{"EUR_USD":{
                     "quote":{"time":"2026-10-21T19:00:00Z","bid":1.1,
                              "ask":1.1001,"spread_pips":1,
                              "pip_size":.0001,"tradeable":True,
                              "account_id":"PRIVATE_DO_NOT_TRANSFER"},
                     "frames":{p:{"completed_at":"2026-10-21T18:45:00Z",
                                    "indicators":{"ema20":1.1}}
                               for p in ("M15","H1")}}},
                 "ranked_review_candidates":[]}
        recent={"EUR_USD":{"frames":{
            p:{"recent_completed_candles":[
                {"time":"2026-10-21T18:45:00Z",
                 "mid":{"o":1.1,"h":1.1002,"l":1.0998,"c":1.1},
                 "bid":{"o":1.0},"account_id":"HIDDEN"}]*12}
            for p in ("M15","H1")}}}
        news={"collected_at":"2026-10-21T19:00:00Z",
              "recent_material_headlines":[],"calendar":{"events":[]},"sources":{}}
        feed=build_public_feed(summary,news,{"signals":[]},{},{},recent)
        pair=feed["pairs"]["EUR_USD"]
        self.assertFalse(pair["deep"])
        self.assertEqual(len(pair["frames"]["M15"]["candles"]),12)
        self.assertEqual(len(pair["frames"]["H1"]["candles"]),12)
        self.assertNotIn("PRIVATE_DO_NOT_TRANSFER",str(feed))
        self.assertNotIn("HIDDEN",str(feed))
        self.assertEqual(feed["signals"],[])

if __name__=="__main__":
    unittest.main()

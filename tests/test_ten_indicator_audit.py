"""Ten-indicator FX review: independent synthetic OHLC regression checks."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from advanced_technicals import advanced, donchian, ichimoku, supertrend
from fx_universe import screen_priority, deep_selection

def bars(n=250, drift=0.00003):
    out=[]
    for i in range(n):
        p=1.1+i*drift
        out.append({"time":f"c{i}","mid":{"o":p-.00003,"h":p+.0002,
                    "l":p-.0002,"c":p}})
    return out

class TenIndicatorTests(unittest.TestCase):
    def test_donchian_uses_prior_bars_not_current(self):
        x=bars(50)
        old=donchian(x)
        x[-1]["mid"]["c"]=old["upper"]+.001
        x[-1]["mid"]["h"]=x[-1]["mid"]["c"]+.0001
        self.assertEqual(donchian(x)["close_breakout"],"UP")
        self.assertAlmostEqual(donchian(x)["upper"],old["upper"])

    def test_ichimoku_no_leading_span_future_leakage(self):
        x=bars()
        first=ichimoku(x)
        # Moving only the most recent bar cannot change spans that were
        # computed 26 bars earlier to appear at today's chart location.
        x[-1]["mid"]["h"]+=.01
        self.assertEqual(ichimoku(x)["cloud_at_current_a"],first["cloud_at_current_a"])
        self.assertEqual(ichimoku(x)["cloud_at_current_b"],first["cloud_at_current_b"])

    def test_supertrend_is_bounded_and_stable(self):
        result=supertrend(bars())
        self.assertIn(result["direction"],("UP","DOWN"))
        self.assertGreater(result["line"],0)
        self.assertFalse(result["flipped_on_last_completed_bar"])
        self.assertIsNone(supertrend(bars(8)))

    def test_selected_ten_indicators_have_valid_descriptors(self):
        obj=advanced(bars())
        self.assertEqual(obj["donchian_20"]["period"],20)
        self.assertEqual(obj["ichimoku_9_26_52"]["forward_shift_bars"],26)
        self.assertEqual(obj["supertrend_10_3"]["multiplier"],3)
        for v in ["bollinger_20_2","stochastic_14_3","structure"]:
            self.assertIsNotNone(obj[v])

    def test_high_spread_is_screened_but_not_auto_approved(self):
        t=advanced(bars())
        indicators={"atr14":.0007,"adx14":24,"rsi14":55,
            "ema20":1.104,"ema50":1.103,"plus_di14":27,
            "minus_di14":15,"macd_12_26_9":{"histogram":.00004}}
        quote={"bid":1.108,"ask":1.109,"spread_pips":10,
               "pip_size":.0001,"tradeable":True}
        f={"indicators":indicators,"advanced":t}
        result=screen_priority(quote,f,f)
        self.assertIsNotNone(result)
        self.assertTrue(result["screening_only"])
        self.assertEqual(len(result["methods_reviewed"]),10)
        self.assertTrue(result["high_relative_spread"]==False)
        quote["ask"]=1.112
        quote["spread_pips"]=40
        big=screen_priority(quote,f,f)
        self.assertIsNotNone(big)
        self.assertTrue(big["high_relative_spread"])
        self.assertLess(big["research_priority"],result["research_priority"])

    def test_12_ranked_finalists_are_deeply_audited(self):
        d={f"A{i:02d}_USD":{"research_priority":80-i} for i in range(25)}
        chosen=deep_selection(d,max_extra=12)
        for i in range(12):
            self.assertIn(f"A{i:02d}_USD",chosen)
        self.assertNotIn("A24_USD",chosen)

if __name__=="__main__":
    unittest.main()

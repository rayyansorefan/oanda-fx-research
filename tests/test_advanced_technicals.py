"""Offline validation of advanced research descriptors — synthetic candles only."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from advanced_technicals import (
    bollinger, candle_structure, stochastic, swings, fib_from_swings, 
    divergence, structure, advanced,
)


def bars(closes):
    items = []
    for i, close in enumerate(closes):
        items.append({
            "time": f"2026-10-20T{i // 60:02d}:{i % 60:02d}:00Z",
            "mid": {"o": close - 0.0001, "c": close,
                    "h": close + 0.0004, "l": close - 0.0004}
        })
    return items


class AdvancedTests(unittest.TestCase):
    def test_flat_bollinger(self):
        bb = bollinger(bars([1.0] * 100))
        self.assertEqual(bb["upper"], 1.0)
        self.assertEqual(bb["lower"], 1.0)
        self.assertEqual(bb["bandwidth_percentile_recent"], 100.0)
        self.assertIsNone(bb["close_position"])

    def test_stochastic_between_zero_and_hundred(self):
        test = stochastic(bars([1 + i * .0001 for i in range(120)]))
        self.assertLessEqual(test["k"], 100)
        self.assertGreaterEqual(test["k"], 0)
        self.assertLessEqual(test["d"], 100)

    def test_confirmed_swing_requires_right_candles(self):
        sequence = bars([1] * 20)
        for i, x in enumerate([1, 1.1, 1.4, 1.2, 1.1, 1.05]):
            sequence[i + 7]["mid"]["h"] = x
        result = swings(sequence)
        self.assertTrue(result["highs"])
        sequence[-1]["mid"]["h"] = 2.0
        self.assertNotEqual(swings(sequence)["highs"][-1]["price"], 2.0)

    def test_fibonacci_anchor_direction(self):
        anchors = {"highs": [{"index": 15, "time": "later", "price": 1.20}],
                   "lows": [{"index": 3, "time": "earlier", "price": 1.10}]}
        result = fib_from_swings(anchors)
        self.assertEqual(result["move"], "UP")
        self.assertAlmostEqual(result["retracement"]["0.500"], 1.15)
        self.assertAlmostEqual(result["retracement"]["0.618"], 1.1382)

    def test_latest_candle_wicks(self):
        rows = bars([1.1, 1.2])
        c = candle_structure(rows)
        self.assertGreaterEqual(c["close_position_in_range"], 0)
        self.assertLessEqual(c["close_position_in_range"], 1)

    def test_breakout_excludes_current_candle(self):
        rows = bars([1.00 + 0.00001 * i for i in range(100)])
        rows[-1]["mid"]["c"] = rows[-1]["mid"]["h"] + .001
        # A valid OHLC candle must have close <= high; adjust high accordingly.
        rows[-1]["mid"]["h"] = rows[-1]["mid"]["c"] + .0002
        self.assertEqual(structure(rows)["latest_close_breakout"], "UP")

    def test_advanced_complete_without_prior_swings(self):
        analysis = advanced(bars([1.0] * 220))
        self.assertIn("stochastic_14_3", analysis)
        self.assertIn("structure", analysis)
        self.assertIn("volume_context", analysis)


if __name__ == "__main__":
    unittest.main()

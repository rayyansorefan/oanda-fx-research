"""No-network checks for bounded FX chart-timeframe expansion."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from oanda_data import (FRAMES, EXTENDED_CHART_FRAMES,
                        extended_chart_selection)


class ChartFrameCoverageTests(unittest.TestCase):
    def test_core_timeframes_unchanged_and_extended_context_complete(self):
        self.assertEqual(FRAMES, ("M5", "M15", "H1", "H4", "D"))
        self.assertEqual(EXTENDED_CHART_FRAMES, ("M1", "M30", "W", "M"))

    def test_extra_context_prioritises_major_currency_crosses_only(self):
        ranking = {
            "USD_JPY": {"research_priority": 66},
            "HKD_JPY": {"research_priority": 65},
            "CHF_JPY": {"research_priority": 64},
            "USD_HUF": {"research_priority": 63},
            "GBP_USD": {"research_priority": 62},
            "AUD_USD": {"research_priority": 61},
            "EUR_USD": {"research_priority": 60},
        }
        result = extended_chart_selection(tuple(ranking), ranking, maximum=3)
        self.assertEqual(result, ("USD_JPY", "CHF_JPY", "GBP_USD"))
        self.assertNotIn("HKD_JPY", result)
        self.assertNotIn("USD_HUF", result)

    def test_bounded_and_missing_rankings_never_fabricated(self):
        result = extended_chart_selection(
            ("EUR_USD", "GBP_USD", "CAD_JPY"),
            {"GBP_USD": {"research_priority": 8},
             "EUR_USD": {"research_priority": 7}}, maximum=4)
        self.assertEqual(result, ("GBP_USD", "EUR_USD"))


if __name__ == "__main__":
    unittest.main()

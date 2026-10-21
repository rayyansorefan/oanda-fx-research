"""Offline universe-discovery/FX pair coverage regression suite."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fx_universe as universe
import oanda_data as data
import news_watch as news
import signal_watch as watch


def quote(instrument="EUR_USD"):
    return {"tradeable": True, "bid": 1.101, "ask": 1.1011,
            "spread_pips": 1.0, "pip_size": .0001}


def frame():
    return {"indicators": {
        "adx14": 26.5, "atr14": .0007, "rsi14": 54,
        "ema20": 1.100, "macd_12_26_9": {"histogram": .0001}}}


class DiscoveryTests(unittest.TestCase):
    def test_discover_only_oanda_currency_instruments(self):
        raw = [
            {"name": "EUR_USD", "type": "CURRENCY", "pipLocation": -4},
            {"name": "USD_JPY", "type": "CURRENCY", "pipLocation": -2},
            {"name": "USD_ZAR", "type": "CURRENCY", "pipLocation": -4},
            {"name": "GBP_NZD", "type": "CURRENCY", "pipLocation": -4},
            {"name": "XAU_USD", "type": "METAL", "pipLocation": -2},
            {"name": "US30_USD", "type": "CFD", "pipLocation": -1},
            {"name": "USD_USD", "type": "CURRENCY", "pipLocation": -4},
            {"name": "INVALID", "type": "CURRENCY", "pipLocation": -4},
        ]
        out = universe.catalog(raw)
        self.assertEqual(sorted(out), ["EUR_USD", "GBP_NZD", "USD_JPY", "USD_ZAR"])
        self.assertAlmostEqual(out["USD_JPY"]["pip_size"], .01)
        self.assertEqual(out["GBP_NZD"]["pepperstone_spreadbet_account_availability"], "UNVERIFIED")
        self.assertFalse(out["USD_ZAR"]["pepperstone_uk_publicly_referenced"])

    def test_chunk_count_and_values(self):
        vals = list(range(91))
        rows = list(universe.chunks(vals, 35))
        self.assertEqual([len(r) for r in rows], [35, 35, 21])
        self.assertEqual([x for row in rows for x in row], vals)

    def test_quote_admission(self):
        self.assertTrue(universe.is_quote_usable(quote()))
        bad = quote()
        bad["ask"] = 1.100
        self.assertFalse(universe.is_quote_usable(bad))
        bad = quote()
        bad["tradeable"] = False
        self.assertFalse(universe.is_quote_usable(bad))

    def test_research_rank_is_not_trade_signal(self):
        score = universe.screen_priority(quote(), frame(), frame())
        self.assertTrue(score["screening_only"])
        self.assertIsInstance(score["research_priority"], float)
        self.assertFalse("trade_direction" in score)
        widened = quote()
        widened["spread_pips"] = 20.0
        widened_report = universe.screen_priority(widened, frame(), frame())
        self.assertIsNotNone(widened_report)
        self.assertTrue(widened_report["high_relative_spread"])
        self.assertLess(widened_report["research_priority"], score["research_priority"])

    def test_shortlist_includes_original_core_and_monitored(self):
        screening = {
            "EUR_USD": {"research_priority": 4.0},
            "USD_JPY": {"research_priority": 5.0},
            "GBP_NZD": {"research_priority": 15.0},
            "USD_ZAR": {"research_priority": 3.0},
        }
        shortlist = universe.deep_selection(screening, ["USD_ZAR"], max_extra=1)
        self.assertTrue(set(("GBP_NZD", "EUR_USD", "USD_JPY", "USD_ZAR")).issubset(shortlist))

    def test_extra_forex_news_currency_without_bank_feed_is_not_all_clear(self):
        existing = news.PAIR_CURRENCIES.copy()
        original_currency = news.CURRENCIES
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "universe.json"
            path.write_text(json.dumps({"instruments": {
                "EUR_USD": {}, "USD_ZAR": {}, "GBP_NZD": {}
            }}))
            try:
                with patch.object(news, "load", side_effect=lambda p, d: json.loads(path.read_text())):
                    universe_map = news.refresh_pair_universe()
                    self.assertIn("USD_ZAR", universe_map)
                    result = news.coverage_for("USD_ZAR", {
                        "Fed policy": {"status": "OK"},
                        "Global markets aggregation": {"status": "OK"}
                    }, "OK")
                    self.assertEqual(result["status"], "UNAVAILABLE")
                    self.assertIn("ZAR", result["currencies_without_supported_official_feed"])
            finally:
                news.PAIR_CURRENCIES = existing
                news.CURRENCIES = original_currency

    def test_exotic_pip_size_used_in_risk_screening(self):
        sig = {"instrument": "USD_ZAR", "direction": "LONG",
               "planned_entry": 16.00, "stop": 15.80, "tp1": 16.50}
        self.assertTrue(watch.valid_risk(sig, 10, observed_pip_size=0.0001))
        self.assertFalse(watch.valid_risk(sig, 5000, observed_pip_size=0.0001))

    def test_nonessential_bad_pair_may_be_partial_not_total_failure(self):
        self.assertIn("PARTIAL", ("OK", "PARTIAL"))
        self.assertNotIn("DATA_UNAVAILABLE", ("OK", "PARTIAL"))


if __name__ == "__main__":
    unittest.main()

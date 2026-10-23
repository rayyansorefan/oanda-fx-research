import unittest
from scripts.export_public_dashboard import build_public_feed

class PublicDashboardFeedTests(unittest.TestCase):
    def sample(self):
        return {
            "summary": {
                "collected_at": "2026-10-21T19:00:00Z",
                "coverage": {
                    "oanda_account_fx_instruments_discovered": 1,
                    "oanda_pairs_with_usable_live_quotes": 1,
                    "oanda_pairs_with_m15_h1_indicators": 1,
                    "detailed_audit_pairs": [],
                },
                "pairs": {
                    "EUR_USD": {
                        "quote": {"time": "2026-10-21T18:59:59Z", "bid": 1.1, "ask": 1.1001,
                                  "spread_pips": 1, "account_id": "MUST_NOT_LEAK"},
                        "frames": {"M15": {"completed_at": "2026-10-21T18:45:00Z",
                                         "indicators": {"ema20": 1.1}}}
                    }
                },
                "ranked_review_candidates": [{"instrument": "EUR_USD", "research_priority": 18}],
            },
            "news": {"collected_at": "2026-10-21T19:00:00Z", "sources": {}, "calendar": {},
                     "recent_material_headlines": []},
        }
    def test_whitelisted_data_no_account_metadata(self):
        obj = self.sample()
        f = build_public_feed(obj["summary"], obj["news"], {"signals":[]}, {}, {})
        self.assertNotIn("MUST_NOT_LEAK", str(f))
        self.assertEqual(f["pairs"]["EUR_USD"]["quote"]["spread_pips"], 1)
        self.assertEqual(f["signals"], [])
        self.assertEqual(f["schema"], 1)
        self.assertEqual(f["coverage"]["screened"], 1)

    def test_research_setups_reject_countertrend_and_create_forward_only_plan(self):
        from scripts.export_public_dashboard import build_research_setups
        indicators = lambda ema20, ema50, **extra: {"ema20": ema20, "ema50": ema50, **extra}
        summary = {
            "ranked_review_candidates": [
                {"instrument": "EUR_JPY", "research_priority": 73,
                 "technical_checks_agree": 5, "potential_direction_not_approved": "SHORT"}
            ],
            "pairs": {"EUR_JPY": {
                "quote": {"time": "2026-10-23T08:02:00Z", "bid": 175.74, "ask": 175.77,
                          "pip_size": 0.01, "spread_pips": 3, "tradeable": True},
                "frames": {
                    "M15": {"indicators": indicators(175.91, 175.93,
                        atr14=.155, resistance20=176.22, support20=175.68, adx14=23)},
                    "H1": {"indicators": indicators(176.04, 176.52, adx14=42)},
                    "H4": {"indicators": indicators(176.81, 177.15)},
                    "D": {"indicators": indicators(177.81, 179.69)}
                }
            }}
        }
        setups = build_research_setups(summary)
        self.assertEqual(len(setups), 1)
        s = setups[0]
        self.assertEqual(s["direction"], "SHORT")
        self.assertFalse(s["approved_entry"])
        self.assertTrue(s["entry_low"] < s["entry_high"] < s["stop"])
        self.assertTrue(s["tp2"] < s["tp1"] < s["entry_low"])
        self.assertGreaterEqual(s["net_rr_estimate"], 1.35)
        summary["pairs"]["EUR_JPY"]["frames"]["H4"]["indicators"]["ema20"] = 180.0
        self.assertEqual(build_research_setups(summary), [])

    def test_feed_budget_never_drops_research_setups(self):
        from scripts.export_public_dashboard import PUBLIC_MAX_BYTES, fit_public_feed_budget, serialized_size
        long_candle = {"t": "2026-10-23T09:00:00Z", "o": 157.1234567, "h": 157.2345678,
                       "l": 157.0123456, "c": 157.1987654}
        payload = {"research_setups": [{"instrument": "USD_JPY",
                                       "status": "CONDITIONAL_RESEARCH_NOT_TRIGGERED"}],
                   "signals": [],
                   "pairs": {
                       str(n): {"deep": True, "frames": {
                           frame: {"candles": [long_candle.copy() for _ in range(70)]}
                           for frame in ("M1", "M5", "M15", "M30", "H1", "H4", "D", "W", "M")}}
                       for n in range(24)}}
        self.assertGreater(serialized_size(payload), PUBLIC_MAX_BYTES)
        fit_public_feed_budget(payload)
        self.assertLessEqual(serialized_size(payload), PUBLIC_MAX_BYTES)
        self.assertEqual(payload["research_setups"][0]["instrument"], "USD_JPY")
        self.assertGreaterEqual(min(len(f["candles"]) for p in payload["pairs"].values()
                                    for f in p["frames"].values()), 6)

    def test_refuses_missing_source(self):
        obj = self.sample()
        obj["summary"]["collected_at"] = None
        with self.assertRaises(ValueError):
            build_public_feed(obj["summary"], obj["news"], {"signals":[]}, {}, {})
    def test_preserve_canonical_signal_fields(self):
        obj = self.sample()
        s={"id":"trial","instrument":"EUR_USD","direction":"LONG",
           "planned_entry":1.1,"stop":1.09,"tp1":1.12,
           "secret_metadata":"SHOULD_NEVER_LEAK"}
        f=build_public_feed(obj["summary"],obj["news"],{"signals":[s]},{},{})
        self.assertEqual(f["signals"][0]["stop"],1.09)
        self.assertNotIn("SHOULD_NEVER_LEAK",str(f))

if __name__ == "__main__":
    unittest.main()

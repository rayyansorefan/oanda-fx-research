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

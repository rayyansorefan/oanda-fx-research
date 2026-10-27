"""Regression checks for long-running hourly FX dashboard publication."""
import unittest
from scripts.export_public_dashboard import fit_public_feed_budget, serialized_size, PUBLIC_MAX_BYTES

class BudgetTests(unittest.TestCase):
    def sample(self):
        candles=[{"t":"2026-10-26T19:15:00Z","o":1.123456,"h":1.123789,
                  "l":1.123111,"c":1.123333,"v":120} for _ in range(75)]
        pairs={f"FX_{i:02d}":{"deep":i<12,"quote":{"time":"2026-10-26T19:32:00Z",
              "bid":1.12345,"ask":1.12355},"frames":{"M15":{"candles":list(candles),
              "indicators":{"ema20":1.123}},"H1":{"candles":list(candles)}}}
              for i in range(64)}
        candidates=[{"id":str(i),"instrument":"EUR_USD","direction":"SHORT",
                     "entry":1.12,"stop":1.13,"tp1":1.10,"created_at":"2026-10-26T19:00:00Z",
                     "tracking":{"history":[{"at":"2026-10-26T19:00:00Z",
                         "price":1.12,"move_pct":0} for _ in range(15)]}}
                    for i in range(70)]
        return {"pairs":pairs,"learning":{"paper_audit":{
            "totals":{"candidates":70},"candidates":candidates}}}

    def test_size_budget_preserves_economic_terms_and_declares_coverage(self):
        payload=self.sample()
        before=[(x["id"],x["entry"],x["stop"],x["tp1"]) for x in
                payload["learning"]["paper_audit"]["candidates"]]
        fit_public_feed_budget(payload)
        self.assertLessEqual(serialized_size(payload),PUBLIC_MAX_BYTES)
        audit=payload["learning"]["paper_audit"]
        window=audit["public_window"]
        self.assertEqual(window["private_total_candidates"],70)
        self.assertEqual(window["published_candidates"],len(audit["candidates"]))
        self.assertEqual(window["truncated"],len(audit["candidates"])<70)
        after=[(x["id"],x["entry"],x["stop"],x["tp1"]) for x in audit["candidates"]]
        self.assertEqual(after,before[-len(after):])
        self.assertEqual(payload["pairs"]["FX_00"]["quote"]["bid"],1.12345)
        self.assertTrue(all(row["tracking"]["history"] for row in audit["candidates"]),
                        "Never publish an empty price history for retained candidates")
        self.assertGreaterEqual(min(len(f["candles"]) for p in payload["pairs"].values()
                                    for f in p["frames"].values()), 6)

    def test_small_feed_does_not_mutate_candidate_history(self):
        p={"pairs":{},"learning":{"paper_audit":{"totals":{"candidates":1},
            "candidates":[{"id":"1","tracking":{"history":[1,2,3]}}]}}}
        fit_public_feed_budget(p)
        self.assertEqual(p["learning"]["paper_audit"]["candidates"][0]["tracking"]["history"],[1,2,3])
        self.assertFalse(p["learning"]["paper_audit"]["public_window"]["truncated"])

if __name__=="__main__":
    unittest.main()

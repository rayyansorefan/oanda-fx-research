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

    def test_full_core_universe_keeps_charts_indicators_and_complete_candidate_index(self):
        from copy import deepcopy
        payload=self.sample()
        base=deepcopy(payload["pairs"]["FX_00"]["frames"]["M15"])
        base["indicators"].update({"adx14":25,"rsi14":55,
            "structure":{"confirmed_swings":{"highs":"HIGHER","lows":"HIGHER"},
                         "last_candle":{"unused_rich_details":"x"*1200},
                         "fibonacci":{"note":"x"*800}},
            "bollinger":{"period":20,"bandwidth_percentile_recent":50}})
        payload["pairs"]={f"FX_{i:02d}":{"deep":True,"quote":{"bid":1.1,"ask":1.1001},
            "frames":{tf:deepcopy(base) for tf in ("M5","M15","H1","H4","D")}} for i in range(68)}
        candidates=payload["learning"]["paper_audit"]["candidates"]
        payload["learning"]["paper_audit"]["candidates"]=[dict(deepcopy(candidates[i%70]),id=str(i)) for i in range(700)]
        payload["learning"]["paper_audit"]["totals"]["candidates"]=700
        fit_public_feed_budget(payload)
        self.assertLessEqual(serialized_size(payload),PUBLIC_MAX_BYTES)
        self.assertEqual(len(payload["learning"]["paper_audit"]["candidate_index"]),700)
        self.assertTrue(payload["public_compaction"]["optional_pattern_details_omitted"])
        for row in payload["pairs"].values():
            self.assertEqual(len(row["frames"]),5)
            for frame in row["frames"].values():
                self.assertGreaterEqual(len(frame["candles"]),6)
                self.assertEqual(frame["indicators"]["adx14"],25)
                self.assertEqual(frame["indicators"]["bollinger"]["bandwidth_percentile_recent"],50)
                self.assertEqual(frame["indicators"]["structure"]["confirmed_swings"]["highs"],"HIGHER")

    def test_extra_deep_charts_compact_context_before_entry_charts(self):
        from unittest.mock import patch
        import scripts.export_public_dashboard as exporter
        # Simulate an already-compact but just-over-budget 68-pair feed.
        # No source or candidate index may disappear to fit the JSON budget.
        bar = {"t":"2026-11-02T07:00:00Z", "o":1.123456,
               "h":1.123789, "l":1.123111, "c":1.123333, "v":120}
        payload = {"pairs": {
            f"FX_{i:02d}": {"deep": True,
                "quote":{"time":"2026-11-02T07:20:00Z",
                         "bid":1.12345, "ask":1.12355},
                "frames": {tf: {"candles":[dict(bar) for _ in range(6)],
                                "indicators":{"ema20":1.123}}
                           for tf in ("M5", "M15", "H1", "H4", "D")}}
            for i in range(68)},
            "learning":{"paper_audit":{"totals":{"candidates":1},
                        "candidates":[{"id":"frozen-1", "entry":1.12,
                                       "stop":1.13,"tp1":1.10}]}}}
        before = serialized_size(payload)
        # Existing stages cannot reduce six-candle deep charts, so the
        # final 6/3 frame fallback must handle this near-limit growth.
        with patch.object(exporter, "PUBLIC_MAX_BYTES", before - 80), \\
             patch.object(exporter, "PUBLIC_HARD_MAX_BYTES", before - 30):
            exporter.fit_public_feed_budget(payload)
            self.assertLessEqual(serialized_size(payload), before - 30)
        self.assertEqual(len(payload["pairs"]), 68)
        self.assertEqual(payload["learning"]["paper_audit"]["candidate_index"][0][0], "frozen-1")
        self.assertTrue(payload["public_compaction"]["reduced_chart_history"])
        for pair in payload["pairs"].values():
            self.assertEqual(len(pair["frames"]["M15"]["candles"]), 6)
            self.assertEqual(len(pair["frames"]["H1"]["candles"]), 6)
            self.assertLessEqual(len(pair["frames"]["H4"]["candles"]), 3)
            self.assertEqual(pair["quote"]["ask"], 1.12355)

    def test_small_feed_does_not_mutate_candidate_history(self):
        p={"pairs":{},"learning":{"paper_audit":{"totals":{"candidates":1},
            "candidates":[{"id":"1","tracking":{"history":[1,2,3]}}]}}}
        fit_public_feed_budget(p)
        self.assertEqual(p["learning"]["paper_audit"]["candidates"][0]["tracking"]["history"],[1,2,3])
        self.assertFalse(p["learning"]["paper_audit"]["public_window"]["truncated"])

    def test_full_index_preserves_first_observed_reference(self):
        p={"pairs":{}, "learning":{"paper_audit":{"totals":{"candidates":2},
            "candidates":[
                {"id":"early", "instrument":"CAD_CHF", "direction":"LONG",
                 "tracking":{"reference_price":0.59012, "quote_at":"2026-10-28T15:00:00Z"}},
                {"id":"later", "instrument":"CAD_CHF", "direction":"LONG",
                 "tracking":{"reference_price":0.59091, "quote_at":"2026-10-28T15:50:00Z"}}
            ]}}}
        fit_public_feed_budget(p)
        audit=p["learning"]["paper_audit"]
        names=audit["candidate_index_fields"]
        self.assertIn("reference_price", names)
        idx=names.index("reference_price")
        self.assertEqual([row[idx] for row in audit["candidate_index"]],
                         [0.59012, 0.59091])

    def test_scored_outcomes_survive_compaction(self):
        payload=self.sample()
        # Force the public rolling window to shrink while keeping an older
        # genuine winner and loser whose evidence must remain visible.
        base=payload["learning"]["paper_audit"]["candidates"]
        rows=[]
        for i in range(400):
            row=dict(base[i % len(base)])
            row["id"]=str(i)
            row["narrative"]="x"*2400
            rows.append(row)
        rows[0]["scored"]=True
        rows[0]["status"]="WIN_TP1"
        rows[1]["scored"]=True
        rows[1]["status"]="LOSS_STOP"
        payload["learning"]["paper_audit"]["candidates"]=rows
        payload["learning"]["paper_audit"]["totals"]={"candidates":400,"scored":2}
        fit_public_feed_budget(payload)
        kept=payload["learning"]["paper_audit"]["candidates"]
        self.assertLess(len(kept),400)
        self.assertEqual({r["id"] for r in kept if r.get("scored")},{"0","1"})
        self.assertEqual({r["status"] for r in kept if r.get("scored")},
                         {"WIN_TP1","LOSS_STOP"})
        self.assertLessEqual(serialized_size(payload),PUBLIC_MAX_BYTES)
    def test_full_index_survives_optional_swing_compaction(self):
        payload = self.sample()
        original = payload["learning"]["paper_audit"]["candidates"]
        # Grow the complete cohort and duplicate optional swing evidence to
        # exercise the final compaction stage without touching trade economics.
        payload["learning"]["paper_audit"]["candidates"] = [
            dict(original[i % len(original)], id=f"full-{i}")
            for i in range(660)
        ]
        payload["learning"]["paper_audit"]["totals"]["candidates"] = 660
        for pair in payload["pairs"].values():
            for frame in pair["frames"].values():
                frame.setdefault("indicators", {})["structure"] = {
                    "highs": "HIGHER", "lows": "HIGHER",
                    "confirmed_swings": {
                        "recent_highs": [{"time": f"2026-10-29T{i:02d}:00:00Z",
                                          "price": 1.12 + i * .001} for i in range(20)],
                        "recent_lows": [{"time": f"2026-10-29T{i:02d}:00:00Z",
                                         "price": 1.10 + i * .001} for i in range(20)]
                    }
                }
        # Represent other required public content that cannot be dropped.
        payload["headlines"] = [{"headline": "X" * 680_000}]
        fit_public_feed_budget(payload)
        audit = payload["learning"]["paper_audit"]
        self.assertEqual(len(audit["candidate_index"]), 660)
        self.assertEqual({row[0] for row in audit["candidate_index"]},
                         {f"full-{i}" for i in range(660)})
        self.assertLessEqual(serialized_size(payload), PUBLIC_MAX_BYTES)
        for pair in payload["pairs"].values():
            for frame in pair["frames"].values():
                structure = frame["indicators"]["structure"]
                self.assertEqual(structure["highs"], "HIGHER")
                self.assertEqual(structure["lows"], "HIGHER")
                self.assertLessEqual(len(structure["confirmed_swings"]["recent_highs"]), 2)
                self.assertLessEqual(len(structure["confirmed_swings"]["recent_lows"]), 2)

if __name__=="__main__":
    unittest.main()

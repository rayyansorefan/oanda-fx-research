import unittest
from scripts.entry_readiness import entry_diagnostics


class EntryReadinessTests(unittest.TestCase):
    def example(self):
        inds = lambda e20,e50,adx: {
            "support20":1.0990, "resistance20":1.1036,
            "ema20":e20,"ema50":e50,"adx14":adx,"atr14":0.0008,
            "plus_di14":32,"minus_di14":13,
            "macd_12_26_9":{"histogram":.0001},
        }
        # Source time 15:02; 14:45 bar independently completed at 15:00.
        return {
          "collected_at":"2026-10-28T15:02:00Z",
          "ranked_review_candidates":[{
             "instrument":"EUR_USD","research_priority":69.2,
             "potential_direction_not_approved":"LONG",
             "adaptive_context":{"block_new_alert":False}
          }],
          "pairs":{
             "EUR_USD":{
                "quote":{"time":"2026-10-28T15:01:30Z",
                         "bid":1.1009,"ask":1.1010,"pip_size":0.0001,
                         "spread_pips":1,"tradeable":True,
                         "OANDA_API_TOKEN":"MUST_NOT_LEAK"},
                "frames":{
                   "M15":{"indicators":inds(1.101,1.100,24),
                      "last_3_completed_candles":[
                       {"time":"2026-10-28T14:30:00Z",
                        "mid":{"o":1.1000,"h":1.1008,"l":1.0997,"c":1.1006}},
                       {"time":"2026-10-28T14:45:00Z",
                        "mid":{"o":1.1006,"h":1.1012,"l":1.1005,"c":1.1009}}]},
                   "H1":{"indicators":inds(1.101,1.100,30)},
                   "H4":{"indicators":inds(1.101,1.100,30)},
                   "D":{"indicators":inds(1.101,1.100,30)}
                }
             }
          }
        }

    def test_forward_only_bar_level_is_not_approval(self):
        report = entry_diagnostics(self.example(),{"status":"PARTIAL"})
        self.assertEqual(len(report),1)
        row = report[0]
        self.assertEqual(row["reference_m15_level"],1.1008)
        self.assertEqual(row["next_m15_check_at"],"2026-10-28T15:15:00Z")
        self.assertEqual(row["frame_alignment"],"4/4")
        self.assertEqual(row["status"],"DIRECTIONAL_RESEARCH_ONLY_NOT_AN_ENTRY")
        self.assertFalse(row["broker_verified"])
        self.assertFalse(row["approved_entry"])
        self.assertTrue(row["previous_m15_break_observed"])
        self.assertIn("NEWS_PARTIAL",[v["code"] for v in row["blockers"]])
        self.assertIn("NO_APPROVED_TRIGGER",[v["code"] for v in row["blockers"]])
        self.assertNotIn("MUST_NOT_LEAK",str(report))

    def test_structural_paper_preview_has_frozen_risk_but_never_approves_trade(self):
        row = entry_diagnostics(self.example(), {"status":"PARTIAL"})[0]
        preview = row["priced_paper_preview"]
        self.assertIsNotNone(preview)
        self.assertEqual(preview["status"], "PRICED_PAPER_RESEARCH_NOT_TRIGGERED")
        self.assertFalse(preview["approved_entry"])
        self.assertFalse(preview["broker_verified"])
        self.assertLess(preview["stop"], preview["entry_zone_low"])
        self.assertLess(preview["entry_zone_low"], preview["entry_zone_high"])
        self.assertLess(preview["entry_zone_high"], preview["tp1"])
        self.assertGreaterEqual(preview["gross_rr_estimate"], 1.50)
        self.assertGreaterEqual(preview["net_rr_estimate"], 1.35)
        self.assertTrue(preview["requires_future_completed_m15"])
        self.assertEqual(preview["next_m15_check_at"], "2026-10-28T15:15:00Z")
        self.assertEqual(preview["expires_at"], "2026-10-28T15:30:00Z")
        self.assertIn("NEWS_PARTIAL", [x["code"] for x in row["blockers"]])
        self.assertIn("NO_APPROVED_TRIGGER", [x["code"] for x in row["blockers"]])

    def test_pair_specific_fast_clock_prevents_false_future_quote_rejection(self):
        s = self.example()
        # The hourly source stays 15:02, but a validated fast refresh at 15:20
        # carries a newer quote and completed M15 candle. It must not be
        # classified as a future market price relative to the hourly clock.
        s["entry_observation_at"] = "2026-10-28T15:20:00Z"
        s["pairs"]["EUR_USD"]["quote"]["time"] = "2026-10-28T15:19:30Z"
        row = entry_diagnostics(s, {"status": "PARTIAL"})[0]
        self.assertNotIn("STALE_QUOTE", [v["code"] for v in row["blockers"]])
        self.assertFalse(row["approved_entry"])
        del s["entry_observation_at"]
        row = entry_diagnostics(s, {"status": "PARTIAL"})[0]
        self.assertIn("STALE_QUOTE", [v["code"] for v in row["blockers"]])

    def test_no_structural_target_or_low_reward_withholds_paper_plan(self):
        s = self.example()
        s["pairs"]["EUR_USD"]["frames"]["M15"]["indicators"]["resistance20"] = 1.1012
        row = entry_diagnostics(s, {"status":"OK"})[0]
        self.assertIsNone(row["priced_paper_preview"])
        self.assertIn("STRUCTURAL_REWARD", [x["code"] for x in row["blockers"]])
        self.assertFalse(row["approved_entry"])

    def test_weak_trend_is_explained_and_not_promoted(self):
        s = self.example()
        s["pairs"]["EUR_USD"]["frames"]["M15"]["indicators"]["adx14"] = 17
        report = entry_diagnostics(s,{"status":"OK"})
        self.assertIn("TREND_STRENGTH",[v["code"] for v in report[0]["blockers"]])
        self.assertFalse(report[0]["approved_entry"])

    def test_missing_candles_and_stale_quote_fail_closed(self):
        s=self.example()
        s["pairs"]["EUR_USD"]["quote"]["time"]="2026-10-28T12:00:00Z"
        s["pairs"]["EUR_USD"]["frames"]["M15"]["last_3_completed_candles"]=[]
        row=entry_diagnostics(s,{"status":"OK"})[0]
        codes=[b["code"] for b in row["blockers"]]
        self.assertIn("STALE_QUOTE",codes)
        self.assertIn("M15_HISTORY",codes)
        self.assertIsNone(row["reference_m15_level"])
        self.assertFalse(row["approved_entry"])

    def test_missing_higher_frames_are_not_reported_as_opposing_trends(self):
        sample = self.example()
        del sample["pairs"]["EUR_USD"]["frames"]["H4"]
        del sample["pairs"]["EUR_USD"]["frames"]["D"]
        row = entry_diagnostics(sample, {"status": "OK"})[0]
        codes = [item["code"] for item in row["blockers"]]
        self.assertIn("HIGHER_FRAME_MISSING", codes)
        self.assertNotIn("HIGHER_FRAME_CONFLICT", codes)
        self.assertIsNone(row["priced_paper_preview"])
        self.assertFalse(row["approved_entry"])

    def test_excluded_pair_not_shown(self):
        s=self.example()
        s["pairs"]["GBP_ZAR"]=s["pairs"]["EUR_USD"]
        s["ranked_review_candidates"].insert(0,{
            "instrument":"GBP_ZAR","research_priority":99,
            "potential_direction_not_approved":"LONG"})
        self.assertFalse(any(x["instrument"]=="GBP_ZAR" for x in entry_diagnostics(s)))

    def test_full_universe_rank_discovers_late_qualifying_research(self):
        """A pair below the old first-five cutoff must still be audited."""
        from copy import deepcopy
        s = self.example()
        template = deepcopy(s["pairs"]["EUR_USD"])
        ranks = []
        for i, name in enumerate(("AUD_USD", "NZD_USD", "GBP_CHF",
                                  "EUR_NZD", "CAD_CHF", "CHF_JPY")):
            source = deepcopy(template)
            # Intentionally conflicting H1: high research priority is NOT
            # evidence of a safe entry or a reason to exclude better setups.
            source["frames"]["H1"]["indicators"]["ema20"] = 1.099
            s["pairs"][name] = source
            ranks.append({"instrument": name, "research_priority": 95-i,
                          "potential_direction_not_approved": "LONG",
                          "adaptive_context": {"block_new_alert": False}})
        s["pairs"]["USD_CAD"] = deepcopy(template)
        ranks.append({"instrument": "USD_CAD", "research_priority": 31,
                      "potential_direction_not_approved": "LONG",
                      "adaptive_context": {"block_new_alert": False}})
        ranks.append(s["ranked_review_candidates"][0])
        s["ranked_review_candidates"] = ranks
        rows = entry_diagnostics(s, {"status": "PARTIAL"}, limit=4)
        names = {row["instrument"] for row in rows}
        self.assertIn("USD_CAD", names)
        self.assertIn("EUR_USD", names)
        good = next(row for row in rows if row["instrument"] == "USD_CAD")
        self.assertEqual(good["frame_alignment"], "4/4")
        self.assertFalse(good["approved_entry"])
        self.assertIn("NEWS_PARTIAL", [b["code"] for b in good["blockers"]])

if __name__=="__main__":
    unittest.main()

"""Offline checks: prospective trade handoff is risk-gated, immutable and no-order."""
import unittest
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from scripts.signal_admission import admit, qualify, news_gate

NOW = datetime(2026, 10, 28, 10, 5, tzinfo=timezone.utc)

def source():
    def indicators():
        return {"ema20": 1.1000, "ema50": 1.0995, "ema200": 1.0980,
                "adx14": 28, "plus_di14": 30, "minus_di14": 12,
                "atr14": .0008, "resistance20": 1.1035, "support20": 1.0975,
                "macd_12_26_9": {"histogram": .00012}}
    bars = [
        {"time":"2026-10-28T09:15:00Z",
         "mid":{"o":1.0996,"h":1.1000,"l":1.0993,"c":1.0998}},
        {"time":"2026-10-28T09:30:00Z",
         "mid":{"o":1.0998,"h":1.1002,"l":1.0995,"c":1.0999}},
        {"time":"2026-10-28T09:45:00Z",
         "mid":{"o":1.0999,"h":1.1002,"l":1.0996,"c":1.1000}},
    ]
    m5 = [
        {"time":"2026-10-28T09:50:00Z",
         "mid":{"o":1.0998,"h":1.1002,"l":1.0997,"c":1.1000}},
        {"time":"2026-10-28T09:55:00Z",
         "mid":{"o":1.1000,"h":1.1003,"l":1.0999,"c":1.1001}},
    ]
    return {"quote":{"time":"2026-10-28T10:04:00Z", "bid":1.1000,
                     "ask":1.1001, "pip_size":.0001,
                     "spread_pips":1.0, "tradeable":True},
            "frames":{"M5":{"indicators":indicators(),
                            "recent_completed_candles":m5},
                      "M15":{"indicators":indicators(),
                             "last_3_completed_candles":bars},
                      "H1":{"indicators":indicators()},
                      "H4":{"indicators":indicators()},
                      "D":{"indicators":indicators()}}}

def news():
    return {"status":"PARTIAL","collected_at":NOW.isoformat(),
            "by_pair":{"EUR_USD":{"calendar_coverage":"OK",
                      "near_high_impact_calendar":[],
                      "recent_relevant_headlines":[],
                      "coverage":{"status":"LIMITED",
                                  "missing_required":[],
                                  "currencies_without_supported_official_feed":[]}}}}

def ranked():
    return {"instrument":"EUR_USD","research_priority":77,
            "potential_direction_not_approved":"LONG",
            "adaptive_context":{"block_new_alert":False}}

class SignalAdmissionTests(unittest.TestCase):
    def test_freezes_future_pullback_with_real_structural_target_and_cost_gate(self):
        plan, reason = qualify("EUR_USD",ranked(),source(),news(),NOW,"EMA20_PULLBACK")
        self.assertIsNone(reason)
        self.assertEqual(plan["target_basis"],
                         "nearest completed M15/H1/H4/D 20-bar support/resistance")
        self.assertEqual(plan["tp1"],1.1035)
        self.assertLess(plan["stop"],plan["planned_entry"])
        self.assertEqual(plan["alert_b"],1.10)
        self.assertGreaterEqual(plan["gross_rr_at_creation"],1.5)
        self.assertGreaterEqual(plan["net_rr_at_spread_cap"],1.35)
        self.assertTrue(plan["research_only"])
        self.assertFalse(plan["broker_verified"])
        self.assertFalse(plan["account_equity_verified"])
        self.assertEqual(plan["max_equity_risk_fraction"],.0025)
        self.assertGreater(datetime.fromisoformat(plan["expires_at"].replace("Z","+00:00")),NOW)

    def test_near_target_never_replaced_by_fictitious_far_target(self):
        s=source()
        s["frames"]["M15"]["indicators"]["resistance20"]=1.1005
        plan, reason=qualify("EUR_USD",ranked(),s,news(),NOW,"EMA20_PULLBACK")
        self.assertIsNone(plan)
        self.assertEqual(reason,"NEAREST_STRUCTURE_TARGET_REWARD_INADEQUATE")

    def test_pair_news_gap_optional_but_material_headline_blocks(self):
        self.assertIsNone(news_gate("EUR_USD",news(),NOW))
        headlines=news()
        headlines["by_pair"]["EUR_USD"]["recent_relevant_headlines"]=[
            {"severity":"REVIEW","published_at":NOW.isoformat()}]
        self.assertEqual(news_gate("EUR_USD",headlines,NOW),
                         "RECENT_MATERIAL_HEADLINE_REVIEW")
        missing=news()
        missing["by_pair"]["EUR_USD"]["coverage"]["missing_required"]=["BoE"]
        self.assertEqual(news_gate("EUR_USD",missing,NOW),
                         "PRIMARY_NEWS_SOURCE_MISSING")

    def test_prospective_momentum_route_and_no_lookahead(self):
        s=source()
        s["quote"]["bid"]=1.10045
        s["quote"]["ask"]=1.10055
        s["frames"]["M15"]["last_3_completed_candles"][-1]["mid"]={
            "o":1.0998,"h":1.10057,"l":1.09975,"c":1.10052}
        plan, reason=qualify("EUR_USD",ranked(),s,news(),NOW,"MOMENTUM_CONTINUATION")
        self.assertIsNone(reason)
        self.assertIn("alert_c",plan)
        self.assertEqual(plan["entry_route"],"MOMENTUM_CONTINUATION")
        self.assertEqual(plan["trigger_timeframe"], "M5")
        self.assertEqual(plan["decision_status"], "CONDITIONAL_PENDING_NEW_M5_TRIGGER")
        self.assertGreater(plan["planned_entry"],plan["alert_c"])
        s["frames"]["M15"]["last_3_completed_candles"][-1]["time"]="2026-10-28T10:00:00Z"
        plan,reason=qualify("EUR_USD",ranked(),s,news(),NOW,"MOMENTUM_CONTINUATION")
        self.assertIsNone(plan)
        self.assertEqual(reason,"RECENT_COMPLETED_M15_MISSING")

    def test_early_three_frame_momentum_admitted_but_not_as_retroactive_trade(self):
        s = source()
        s["quote"]["bid"] = 1.10045
        s["quote"]["ask"] = 1.10055
        s["frames"]["D"]["indicators"]["ema20"] = 1.0980
        s["frames"]["D"]["indicators"]["ema50"] = 1.0995  # Daily disagrees
        s["frames"]["M15"]["last_3_completed_candles"][-1]["mid"] = {
            "o": 1.10010, "h": 1.10068, "l": 1.09985, "c": 1.10053}
        plan, why = qualify("EUR_USD",ranked(),s,news(),NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(why)
        self.assertTrue(plan["technical_context"]["conditional_daily_exception"])
        self.assertFalse(plan["technical_context"]["full_frame_alignment"])
        self.assertEqual(plan["technical_context"]["continuation_min_body"], .50)
        self.assertEqual(plan["technical_context"]["continuation_min_close"], .70)
        self.assertTrue(plan["planned_entry"] < plan["tp1"])
        self.assertGreaterEqual(plan["net_rr_at_spread_cap"], 1.35)
        self.assertFalse(plan["broker_verified"])
        self.assertEqual(plan["decision_status"], "CONDITIONAL_PENDING_NEW_M5_TRIGGER")
        self.assertIn("alert_c", plan)
        # The same daily conflict cannot open the less stringent pullback route.
        pullback, why = qualify("EUR_USD",ranked(),s,news(),NOW,"EMA20_PULLBACK")
        self.assertIsNone(pullback)
        self.assertEqual(why, "FOUR_FRAME_TREND_CONFLICT")

        weakened = deepcopy(s)
        weakened["frames"]["H1"]["indicators"]["adx14"] = 20
        plan, why = qualify("EUR_USD",ranked(),weakened,news(),NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(plan)
        self.assertEqual(why,"FOUR_FRAME_TREND_CONFLICT")

        countertrend = deepcopy(s)
        countertrend["frames"]["H4"]["indicators"]["ema20"] = 1.0980
        countertrend["frames"]["H4"]["indicators"]["ema50"] = 1.0995
        plan, why = qualify("EUR_USD",ranked(),countertrend,news(),NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(plan)
        self.assertEqual(why,"FOUR_FRAME_TREND_CONFLICT")

        thin = deepcopy(s)
        thin["frames"]["M15"]["indicators"]["resistance20"] = 1.1010
        thin["frames"]["H1"]["indicators"]["resistance20"] = 1.1010
        thin["frames"]["H4"]["indicators"]["resistance20"] = 1.1010
        thin["frames"]["D"]["indicators"]["resistance20"] = 1.1010
        plan, why = qualify("EUR_USD",ranked(),thin,news(),NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(plan)
        self.assertEqual(why,"NEAREST_STRUCTURE_TARGET_REWARD_INADEQUATE")

        unverified, why = qualify("EUR_USD",ranked(),s,{"status":"PARTIAL"},
                                NOW,"MOMENTUM_CONTINUATION")
        self.assertIsNone(unverified)
        self.assertEqual(why,"NEWS_NOT_VERIFIED")

    def test_missing_m5_data_blocks_fast_entry_instead_of_faking_confirmation(self):
        s = source()
        s["quote"].update(bid=1.10045, ask=1.10055)
        s["frames"]["M15"]["last_3_completed_candles"][-1]["mid"] = {
            "o": 1.0998, "h": 1.10057, "l": 1.09975, "c": 1.10052}
        del s["frames"]["M5"]
        plan, why = qualify("EUR_USD", ranked(), s, news(), NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(plan)
        self.assertEqual(why, "RECENT_COMPLETED_M5_MISSING")

    def test_weak_h4_opposition_allows_strong_momentum_but_not_strong_h4_trend(self):
        s = source()
        s["quote"].update(bid=1.10045, ask=1.10055)
        s["frames"]["M15"]["last_3_completed_candles"][-1]["mid"] = {
            "o": 1.0998, "h": 1.10057, "l": 1.09975, "c": 1.10052}
        s["frames"]["H4"]["indicators"].update(ema20=1.0980, ema50=1.0995, adx14=17)
        plan, why = qualify("EUR_USD", ranked(), s, news(), NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(why)
        self.assertTrue(plan["technical_context"]["conditional_weak_h4_exception"])
        s["frames"]["H4"]["indicators"]["adx14"] = 24
        plan, why = qualify("EUR_USD", ranked(), s, news(), NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(plan)
        self.assertEqual(why, "FOUR_FRAME_TREND_CONFLICT")

    def test_bounded_extended_momentum_requires_strong_evidence_and_target(self):
        s = source()
        s["quote"].update(bid=1.10148, ask=1.10158)
        s["frames"]["M15"]["last_3_completed_candles"][-1]["mid"] = {
            "o": 1.1000, "h": 1.10163, "l": 1.0999, "c": 1.10154}
        for tf in ("M15", "H1", "H4", "D"):
            s["frames"][tf]["indicators"]["resistance20"] = 1.1080
        plan, why = qualify("EUR_USD", ranked(), s, news(), NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(why)
        self.assertTrue(plan["technical_context"]["extension_exception"])
        self.assertEqual(plan["technical_context"]["extension_cap_atr"], 2.0)
        s["frames"]["H1"]["indicators"]["adx14"] = 21
        plan, why = qualify("EUR_USD", ranked(), s, news(), NOW,
                            "MOMENTUM_CONTINUATION")
        self.assertIsNone(plan)
        self.assertEqual(why, "REVERSAL_OR_EXHAUSTION")

    def test_existing_original_immutable_and_expiry_archived(self):
        summary={"status":"OK","collected_at":NOW.isoformat(),
                 "ranked_review_candidates":[ranked()],
                 "pairs":{"EUR_USD":source()}}
        a,archive,audit=admit(summary,news(),{"signals":[]})
        self.assertEqual(len(a["signals"]),1)
        frozen=deepcopy(a["signals"][0])
        updated, archive, _=admit(summary,news(),a,archive)
        self.assertEqual(updated["signals"],[frozen])
        tomorrow=deepcopy(summary)
        tomorrow["collected_at"]=(NOW+timedelta(days=1)).isoformat()
        tomorrow["ranked_review_candidates"]=[]
        cleaned,archive,_=admit(tomorrow,news(),updated,archive)
        self.assertEqual(cleaned["signals"],[])
        self.assertEqual(archive["signals"][0],frozen)

    def test_no_trade_on_trend_conflict_costs_or_unverified_news(self):
        q=source()
        q["frames"]["D"]["indicators"]["ema20"]=1.098
        plan,reason=qualify("EUR_USD",ranked(),q,news(),NOW,"EMA20_PULLBACK")
        self.assertIsNone(plan)
        self.assertEqual(reason,"FOUR_FRAME_TREND_CONFLICT")
        q=source()
        q["quote"]["spread_pips"]=9
        plan,reason=qualify("EUR_USD",ranked(),q,news(),NOW,"EMA20_PULLBACK")
        self.assertIsNone(plan)
        self.assertEqual(reason,"SPREAD_TOO_HIGH_RELATIVE_TO_VOLATILITY")
        no_news={"status":"PARTIAL","collected_at":NOW.isoformat()}
        plan,reason=qualify("EUR_USD",ranked(),source(),no_news,NOW,"EMA20_PULLBACK")
        self.assertIsNone(plan)
        self.assertEqual(reason,"PAIR_NEWS_MISSING")

if __name__=="__main__":
    unittest.main()

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
    return {"quote":{"time":"2026-10-28T10:04:00Z", "bid":1.1000,
                     "ask":1.1001, "pip_size":.0001,
                     "spread_pips":1.0, "tradeable":True},
            "frames":{"M15":{"indicators":indicators(),
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
        self.assertGreater(plan["planned_entry"],plan["alert_c"])
        s["frames"]["M15"]["last_3_completed_candles"][-1]["time"]="2026-10-28T10:00:00Z"
        plan,reason=qualify("EUR_USD",ranked(),s,news(),NOW,"MOMENTUM_CONTINUATION")
        self.assertIsNone(plan)
        self.assertEqual(reason,"RECENT_COMPLETED_M15_MISSING")

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


    def test_v2_paper_only_removes_m15_adx_and_primary_feed_gap_not_manual_safeguards(self):
        s=source()
        s["frames"]["M15"]["indicators"]["adx14"]=8
        n=news()
        n["by_pair"]["EUR_USD"]["coverage"]["missing_required"]=["unavailable official source"]
        champion, strict=qualify("EUR_USD",ranked(),s,n,NOW,"EMA20_PULLBACK")
        self.assertIsNone(champion)
        self.assertEqual(strict,"ADX_TREND_STRENGTH")
        v1, v1_reason=qualify("EUR_USD",ranked(),s,n,NOW,"EMA20_PULLBACK",policy="challenger_v1")
        self.assertIsNone(v1)
        self.assertEqual(v1_reason,"PRIMARY_NEWS_SOURCE_MISSING")
        v2, v2_reason=qualify("EUR_USD",ranked(),s,n,NOW,"EMA20_PULLBACK",policy="challenger_v2")
        self.assertIsNone(v2_reason)
        self.assertTrue(v2["paper_only"])
        self.assertEqual(v2["admission_policy"],"challenger_v2")
        self.assertTrue(v2["primary_news_coverage_unverified"])
        self.assertTrue(v2["manual_news_clearance_required"])
        self.assertGreaterEqual(v2["gross_rr_at_creation"],1.50)
        self.assertGreaterEqual(v2["net_rr_at_spread_cap"],1.35)
        self.assertEqual(v2["max_equity_risk_fraction"],.0025)
        self.assertTrue(v2["technical_context"]["full_frame_alignment"])
        n["by_pair"]["EUR_USD"]["recent_relevant_headlines"]=[
            {"severity":"REVIEW","published_at":NOW.isoformat()}]
        veto, reason=qualify("EUR_USD",ranked(),s,n,NOW,"EMA20_PULLBACK",policy="challenger_v2")
        self.assertIsNone(veto)
        self.assertEqual(reason,"RECENT_MATERIAL_HEADLINE_REVIEW")
        n["by_pair"]["EUR_USD"]["recent_relevant_headlines"]=[]
        n["by_pair"]["EUR_USD"]["near_high_impact_calendar"]=[{"title":"Policy decision"}]
        veto, reason=qualify("EUR_USD",ranked(),s,n,NOW,"EMA20_PULLBACK",policy="challenger_v2")
        self.assertIsNone(veto)
        self.assertEqual(reason,"EVENT_CALENDAR_OR_BLACKOUT")

if __name__=="__main__":
    unittest.main()

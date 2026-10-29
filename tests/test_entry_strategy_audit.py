"""Regression tests for the read-only independent study; no live data or broker calls."""
import unittest
from datetime import datetime, timezone

from scripts.entry_strategy_audit import gate_analysis, validation_assessment


def row(arm, day, scored=True, loss=False):
    decision = day + "T08:05:00Z"
    if arm == "champion":
        return {"policy": arm, "pair": "EUR_USD", "route": "EMA20_PULLBACK",
                "decision_at": decision, "observation_scope": "PAIR_SPECIFIC",
                "common_deep_history_available": True, "plan": None}
    plan = {"id": "PLAN-" + day, "instrument": "EUR_USD", "direction": "LONG",
            "created_at": day + "T08:00:00Z", "planned_entry": 1.10,
            "stop": 1.09, "tp1": 1.12, "pip_size": 0.0001}
    result = {"status": "LOSS_STOP" if loss else "WIN_TP1", "scored": scored,
              "entry": 1.10, "exit": 1.09 if loss else 1.12,
              "entry_at": day + "T09:00:00Z", "at": day + "T10:00:00Z",
              "broker_costs_verified": True, "financing_cost_verified": True,
              "additional_broker_cost_r": 0.0}
    if loss:
        result["evidence"] = {"exit": {"bid": {"o": 1.09}}}
    return {"policy": arm, "pair": "EUR_USD", "route": "EMA20_PULLBACK",
            "decision_at": decision, "observation_scope": "PAIR_SPECIFIC",
            "common_deep_history_available": True, "plan": plan, "result": result}


class GateAuditTests(unittest.TestCase):
    def test_isolated_ablation_does_not_override_other_safety_gates(self):
        raw = {"source_at": "2026-10-29T08:13:14Z", "rows": [
            {"pair": "EUR_USD", "route": "EMA20_PULLBACK",
             "checks": {"news_gate": False, "net_reward_minimum": True, "adx_M15": True}},
            {"pair": "GBP_USD", "route": "EMA20_PULLBACK",
             "checks": {"news_gate": False, "net_reward_minimum": False, "adx_M15": True}},
        ]}
        result = gate_analysis(raw)
        self.assertEqual(result["counterfactual_arms"]["champion_all_gates"]["counterfactual_pair_routes"], 0)
        self.assertEqual(result["counterfactual_arms"]["without_news_gate_diagnostic_only"]["counterfactual_pair_routes"], 1)
        self.assertFalse(result["counterfactual_arms"]["without_news_gate_diagnostic_only"]["approval_allowed"])
        self.assertEqual(result["isolated_only_blocker"]["news_gate"], 1)
        self.assertEqual(result["failures"]["news_gate"], 2)



    def test_joint_paper_relaxation_ignores_only_missing_primary_source_not_known_risk(self):
        safe = {"adx_M15": False, "news_gate": False,
                "net_reward_minimum": True, "gross_reward_minimum": True,
                "relative_spread_cap": True, "trend_D": True}
        rows = [
            {"pair":"NZD_CHF","direction":"SHORT","route":"MOMENTUM_CONTINUATION",
             "checks":safe,"news_reason":"PRIMARY_NEWS_SOURCE_MISSING",
             "economic_feasibility_only":{"entry_reference":0.46486,"net_rr":1.62}},
            {"pair":"EUR_USD","direction":"LONG","route":"MOMENTUM_CONTINUATION",
             "checks":safe,"news_reason":"RECENT_MATERIAL_HEADLINE_REVIEW"},
            {"pair":"AUD_CAD","direction":"LONG","route":"EMA20_PULLBACK",
             "checks":{**safe,"net_reward_minimum":False},
             "news_reason":"PRIMARY_NEWS_SOURCE_MISSING"},
        ]
        result = gate_analysis({"source_at":"2026-10-29T08:39:56Z","rows":rows})
        arm=result["counterfactual_arms"]["paper_without_m15_adx_and_missing_primary_feed"]
        self.assertEqual(arm["counterfactual_pair_routes"],1)
        self.assertFalse(arm["approval_allowed"])
        self.assertEqual(arm["paper_watch_examples"][0]["pair"],"NZD_CHF")
        self.assertEqual(arm["paper_watch_examples"][0]["news_status"],"SOURCE_UNVERIFIED")
        self.assertEqual(arm["paper_watch_examples"][0]["status"],
                         "PAPER_ONLY_NOT_A_FUTURE_TRIGGER_OR_MANUAL_ENTRY")

class ProspectiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.days = ("2026-10-30", "2026-11-02")
        self.protocol = {
            "id": "TEST-FIXED-COHORT",
            "holdout_start_at": "2026-10-29T08:30:00Z",
            "minimum_scored_days_per_arm": 2,
            "minimum_independent_scored_pair_days_per_arm": 2,
            "slippage_pips_per_side": [0.5], "bootstrap_resamples": 200}
        self.now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        self.delivery = {"events": {}}
        for d in self.days:
            self.delivery["events"]["RECEIPT-" + d] = {
                "plan_id": "PLAN-" + d, "verified_user_delivery_at": d + "T08:45:00Z",
                "detected_at": d + "T08:35:00Z",
                "verification_kind": "USER_ACKNOWLEDGEMENT",
                "verification_source_reference": "auditable-evidence-" + d}

    def study(self, loss=False):
        return {"records": [row(arm, day, loss=loss)
                            for day in self.days for arm in ("champion", "challenger_v1")]}

    def test_zero_trade_champion_is_abstention_not_permanent_block(self):
        first = validation_assessment(self.study(), self.protocol, self.delivery, now=self.now)
        self.assertEqual(first["common_eligible_observation_days"], 2)
        self.assertEqual(first["arms"]["champion"]["plans_created"], 0)
        self.assertEqual(first["arms"]["champion"]["abstaining_common_days"], 2)
        self.assertEqual(first["arms"]["challenger_v1"]["scored_unique_currency_exposure_days"], 2)
        self.assertGreater(first["paired_day_cluster_interval"]["lower_95pct_r"], 0)
        self.assertEqual(first["decision"], "INSUFFICIENT_EVIDENCE")
        attestation = {"independent_reviewer_approval": True, "source_reference": "review-2026-11-03",
                       "reviewer": "independent reviewer",
                       "cohort_sha256": first["cohort_sha256"]}
        second = validation_assessment(self.study(), self.protocol, self.delivery,
                                       review=attestation, now=self.now)
        self.assertEqual(second["decision"], "PASS")
        self.assertTrue(second["trading_performance_validated"])
        self.assertFalse(second["automatic_promotion_allowed"])

    def test_negative_challenger_fails_without_requiring_champion_fills(self):
        report = validation_assessment(self.study(loss=True), self.protocol, self.delivery, now=self.now)
        self.assertEqual(report["decision"], "FAIL")
        self.assertLess(report["paired_day_cluster_interval"]["upper_95pct_r"], 0)

    def test_missing_user_receipt_prevents_pass(self):
        first = validation_assessment(self.study(), self.protocol, {"events": {}}, now=self.now)
        review = {"independent_reviewer_approval": True, "source_reference": "review-2026-11-03",
                  "reviewer": "independent reviewer", "cohort_sha256": first["cohort_sha256"]}
        report = validation_assessment(self.study(), self.protocol, {"events": {}},
                                       review=review, now=self.now)
        self.assertEqual(report["decision"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(report["evidence"]["user_receipts_before_entry_complete"])


if __name__ == "__main__":
    unittest.main()

import unittest

from strategy_feedback import build_feedback, signal_policy


class StrategyFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.short = {"instrument": "GBP_USD", "direction": "SHORT",
                      "strategy": "M15_REJECTION", "alert_b": 1.3355}

    def test_broker_screenshots_are_recorded_but_not_trained(self):
        book = {"candidates": {}, "updated_at": "2026-10-26T14:00:00Z"}
        broker = {"trades": [
            {"status": "CLOSED", "net_pnl_gbp": 8.95,
             "signal_match": "PENDING", "signal_id": None},
            {"status": "CLOSED", "net_pnl_gbp": -2.00,
             "signal_match": "PENDING", "signal_id": None},
            {"status": "OPEN_AT_SNAPSHOT", "net_pnl_gbp": 1000,
             "signal_match": "PENDING", "signal_id": None},
        ]}
        report = build_feedback(book, broker)
        self.assertEqual(report["broker"]["closed_count"], 2)
        self.assertEqual(report["broker"]["profitable_count"], 1)
        self.assertEqual(report["broker"]["net_profit_gbp"], 6.95)
        self.assertEqual(report["broker"]["unlinked_count"], 2)
        self.assertEqual(report["broker"]["included_in_strategy_weighting"], 0)
        self.assertEqual(signal_policy(self.short, report)["priority_bias"], 0)

    def test_unapproved_audnzd_stopout_is_flagged_without_model_weight_changes(self):
        broker = {"trades": [
            {"instrument": "AUD_NZD", "status": "CLOSED", "net_pnl_gbp": -7.0,
             "closed_at": "2026-10-26T16:22:59Z",
             "entry_classification": "UNAPPROVED_RESEARCH_WATCH_EXECUTED",
             "execution_review": {"entry_deviation_pips": 4.0,
                                  "original_risk_gate": "FAILED_SPREAD_ADJUSTED_TP1_RR"},
             "signal_id": None, "signal_match": "PENDING"},
            {"instrument": "GBP_USD", "status": "CLOSED", "net_pnl_gbp": 8.95,
             "signal_id": None, "signal_match": "PENDING"},
        ]}
        feedback = build_feedback({"candidates": {}}, broker)
        self.assertEqual(feedback["broker"]["closed_count"], 2)
        self.assertEqual(feedback["broker"]["loss_count"], 1)
        self.assertEqual(feedback["broker"]["net_profit_gbp"], 1.95)
        self.assertEqual(feedback["broker"]["unapproved_watch_stopouts"], 1)
        self.assertEqual(feedback["broker"]["pair_observations"]["AUD_NZD"]["unapproved_watch_losses"], 1)
        self.assertEqual(feedback["broker"]["execution_lessons"][0]["planned_entry_deviation_pips"], 4.0)
        self.assertEqual(feedback["broker"]["included_in_strategy_weighting"], 0)
        policy = signal_policy({**self.short, "instrument": "AUD_NZD"}, feedback)
        self.assertEqual(policy["priority_bias"], 0)
        self.assertTrue(policy["manual_execution_review_required"])
        self.assertIn("non-approved paper watch", policy["execution_review"])
        self.assertFalse(signal_policy(self.short, feedback)["manual_execution_review_required"])

    def test_small_positive_sample_cannot_boost_research_alert(self):
        feedback = {"paper_strategy": {"M15_REJECTION / ROUTE_B":
                    {"count": 5, "wins": 5, "paper_priority_bias": 0}}}
        result = signal_policy(self.short, feedback)
        self.assertEqual(result["priority_bias"], 0)
        self.assertEqual(result["status"], "NEUTRAL_INSUFFICIENT_OR_MIXED_EVIDENCE")

    def test_robust_negative_route_suppresses_research_priority(self):
        feedback = {"paper_strategy": {"M15_REJECTION / ROUTE_B":
                    {"count": 40, "wins": 8, "paper_priority_bias": -1}}}
        result = signal_policy(self.short, feedback)
        self.assertEqual(result["priority_bias"], -1)
        self.assertEqual(result["status"], "HISTORICAL_PAPER_CAUTION")

    def test_positive_single_route_is_advisory_not_entry_clearance(self):
        feedback = {"paper_strategy": {"M15_REJECTION / ROUTE_B":
                    {"count": 44, "wins": 30, "paper_priority_bias": 1}}}
        result = signal_policy(self.short, feedback)
        self.assertEqual(result["priority_bias"], 1)
        self.assertIn("NOT_ENTRY_APPROVAL", result["status"])

    def test_mixed_routes_do_not_gain_from_one_route_wins(self):
        signal = {**self.short, "alert_a": 1.3351}
        feedback = {"paper_strategy": {"M15_REJECTION / ROUTE_B":
                    {"count": 44, "paper_priority_bias": 1}}}
        self.assertEqual(signal_policy(signal, feedback)["priority_bias"], 0)


if __name__ == "__main__":
    unittest.main()

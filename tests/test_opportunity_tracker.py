"""No-network regressions for forward-only FX entry opportunity monitoring."""
import copy
import unittest
from unittest.mock import patch
from scripts import opportunity_tracker as tracker

T0 = "2026-10-27T09:14:00Z"
Q0 = "2026-10-27T09:13:00Z"


def snapshot(at=T0, quote_at=Q0, bid=1.11600, ask=1.11608):
    return {
        "collected_at": at,
        "pairs": {"EUR_USD": {
            "quote": {"time": quote_at, "bid": bid, "ask": ask,
                      "pip_size": .0001, "tradeable": True},
            "frames": {"M15": {
                "indicators": {"atr14": .0005},
                "last_3_completed_candles": [{
                    "time": "2026-10-27T08:45:00Z",
                    "mid": {"o": 1.1164, "h": 1.1165, "l": 1.1159, "c": 1.1160}}]}
            }}
        }
    }


def plan():
    return [{
        "instrument": "EUR_USD", "direction": "SHORT",
        "entry_route": "MOMENTUM_CONTINUATION",
        "impulse_m15_started_at": "2026-10-27T08:45:00Z",
        "entry_low": 1.1159, "entry_high": 1.1161,
        "stop": 1.1175, "tp1": 1.113, "tp2": 1.112,
        "approved_entry": False,
    }]


class OpportunityLedgerTests(unittest.TestCase):
    def test_new_prospective_opportunity_is_immutable_and_not_a_trade(self):
        with patch.object(tracker, "build_research_setups", return_value=plan()):
            original = tracker.update_ledger(snapshot(), {})
            self.assertEqual(original["total_registered"], 1)
            first = next(iter(original["opportunities"].values()))
            self.assertFalse(first["scored"])
            self.assertFalse(first["entry_confirmed"])
            self.assertEqual(first["tracking_status"], "WATCH_ONLY_UNTRIGGERED")
            self.assertIsNone(first["hypothetical_move_pct"])
            copy_first = copy.deepcopy(first)
            repeat = tracker.update_ledger(snapshot(), original)
            self.assertEqual(repeat["total_registered"], 1)
            unchanged = next(iter(repeat["opportunities"].values()))
            self.assertEqual(unchanged, copy_first)

        with patch.object(tracker, "build_research_setups", return_value=[]):
            future = tracker.update_ledger(snapshot(
                "2026-10-27T10:14:00Z", "2026-10-27T10:13:00Z",
                bid=1.11390, ask=1.11400), repeat)
        row = next(iter(future["opportunities"].values()))
        self.assertEqual(row["stop"], copy_first["stop"])
        self.assertEqual(row["entry_low"], copy_first["entry_low"])
        self.assertEqual(row["first_seen_at"], copy_first["first_seen_at"])
        self.assertEqual(row["tracking_status"], "FAVOURABLE_MOVE_WITHOUT_VERIFIED_ENTRY")
        self.assertGreater(row["hypothetical_move_pct"], 0)
        self.assertFalse(row["scored"])  # even when price passed TP, no fill
        self.assertFalse(row["entry_confirmed"])
        original_window = copy.deepcopy(row["in_window_audit"])
        self.assertEqual(original_window["coverage"], "COMPLETE_FROM_REGISTRATION")
        self.assertEqual(original_window["samples"], 1)
        self.assertTrue(original_window["first_favourable_at"])
        self.assertEqual(tracker.public_view(future)["lifecycle_audit"]["ever_meaningful_favourable"], 1)

        with patch.object(tracker, "build_research_setups", return_value=[]):
            expired = tracker.update_ledger(snapshot(
                "2026-10-27T11:14:00Z", "2026-10-27T11:13:00Z",
                bid=1.1139, ask=1.1140), future)
        self.assertEqual(next(iter(expired["opportunities"].values()))[
            "tracking_status"], "EXPIRED_RESEARCH_NO_VERIFIED_FILL")
        self.assertEqual(next(iter(expired["opportunities"].values()))[
            "in_window_audit"], original_window)  # After-expiry follow-through cannot rewrite original evidence

    def test_extended_retest_expires_on_source_clock_not_four_hours_later(self):
        setup = [{**plan()[0], "direction": "LONG",
                  "entry_route": "EMA20_PULLBACK",
                  "entry_low": 1.1159, "entry_high": 1.1161,
                  "stop": 1.1145, "tp1": 1.118,
                  "requires_future_retest": True,
                  "expires_at": "2026-10-27T09:30:00Z"}]
        with patch.object(tracker, "build_research_setups", return_value=setup):
            registered = tracker.update_ledger(snapshot(), {})
        self.assertEqual(registered["total_registered"], 1)
        row = next(iter(registered["opportunities"].values()))
        self.assertEqual(row["expires_at"], "2026-10-27T09:30:00+00:00")
        self.assertFalse(row["scored"])
        with patch.object(tracker, "build_research_setups", return_value=[]):
            after = tracker.update_ledger(snapshot(
                "2026-10-27T10:14:00Z", "2026-10-27T10:13:00Z",
                bid=1.1190, ask=1.1191), registered)
        expired = next(iter(after["opportunities"].values()))
        self.assertEqual(expired["tracking_status"],
                         "EXPIRED_RESEARCH_NO_VERIFIED_FILL")
        self.assertEqual(expired["in_window_audit"]["samples"], 0)
        # A scanner delayed beyond the original source expiry must not
        # register a retroactive opportunity in the first place.
        with patch.object(tracker, "build_research_setups", return_value=setup):
            late = tracker.update_ledger(snapshot(
                "2026-10-27T09:40:00Z", "2026-10-27T09:39:00Z"), {})
        self.assertEqual(late["total_registered"], 0)

    def test_legacy_rolling_history_is_explicitly_incomplete(self):
        legacy = {
            "first_seen_at": "2026-10-28T08:00:00Z",
            "expires_at": "2026-10-28T12:00:00Z",
            "meaningful_move_threshold_pct": 0.03,
            "observations": [
                {"at": "2026-10-28T11:00:00Z", "move_pct": 0.05},
                {"at": "2026-10-28T13:00:00Z", "move_pct": 0.50},
            ],
        }
        audit = tracker._prepare_in_window(legacy)
        self.assertEqual(audit["coverage"], "LEGACY_ROLLING_HISTORY_INCOMPLETE")
        self.assertEqual(audit["samples"], 1)
        self.assertEqual(audit["best_pct"], 0.05)
        self.assertTrue(audit["first_favourable_at"])
        self.assertIsNone(audit["first_adverse_at"])

    def test_forward_candle_never_backdates_and_never_claims_a_fill(self):
        setup = [{**plan()[0], "direction": "LONG",
                  "entry_route": "EMA20_PULLBACK",
                  "entry_low": 1.1159, "entry_high": 1.1161,
                  "stop": 1.1145, "tp1": 1.1180}]
        with patch.object(tracker, "build_research_setups", return_value=setup):
            book = tracker.update_ledger(snapshot(), {})
        row = next(iter(book["opportunities"].values()))
        self.assertEqual(row["entry_pattern_status"],
                         "AWAITING_INDEPENDENT_POST_PLAN_CANDLE")
        # This candle closes AFTER the idea, but started BEFORE it:
        # using it as a trigger would be look-ahead contamination.
        old = snapshot("2026-10-27T09:20:00Z", "2026-10-27T09:19:00Z")
        old["pairs"]["EUR_USD"]["frames"]["M15"]["last_3_completed_candles"] = [{
            "time": "2026-10-27T09:00:00Z",
            "mid": {"o": 1.1160, "h": 1.1166, "l": 1.1159, "c": 1.1164}}]
        with patch.object(tracker, "build_research_setups", return_value=[]):
            book = tracker.update_ledger(old, book)
        self.assertEqual(row["entry_pattern_status"],
                         "AWAITING_INDEPENDENT_POST_PLAN_CANDLE")
        future = snapshot("2026-10-27T09:46:00Z", "2026-10-27T09:45:30Z")
        future["pairs"]["EUR_USD"]["frames"]["M15"]["last_3_completed_candles"] = [
            {"time": "2026-10-27T09:00:00Z",
             "mid": {"o": 1.1160, "h": 1.1166, "l": 1.1159, "c": 1.1164}},
            {"time": "2026-10-27T09:15:00Z",
             "mid": {"o": 1.11600, "h": 1.11642, "l": 1.11594, "c": 1.11630}},
            {"time": "2026-10-27T09:30:00Z",
             "mid": {"o": 1.1162, "h": 1.11655, "l": 1.1162, "c": 1.11640}}
        ]
        with patch.object(tracker, "build_research_setups", return_value=[]):
            book = tracker.update_ledger(future, book)
        row = next(iter(book["opportunities"].values()))
        self.assertEqual(row["entry_pattern_status"],
                         "FORWARD_CANDLE_OBSERVED_PAPER_ONLY")
        self.assertEqual(row["entry_pattern_at"], "2026-10-27T09:30:00+00:00")
        self.assertEqual(row["entry_pattern_close"], 1.1163)
        self.assertFalse(row["entry_confirmed"])
        self.assertFalse(row["scored"])
        self.assertEqual(row["tracking_status"],
                         "FORWARD_PATTERN_OBSERVED_NO_APPROVED_FILL")
        self.assertEqual(tracker.public_view(book)["recent"][0][
            "entry_pattern_at"], row["entry_pattern_at"])
        self.assertEqual(tracker.public_view(book)["lifecycle_audit"][
            "forward_entry_patterns_observed"], 1)

    def test_invalidation_inside_bar_does_not_count_as_entry_pattern(self):
        row = {"first_seen_at": Q0, "expires_at": "2026-10-27T10:00:00Z",
               "direction": "LONG", "route": "EMA20_PULLBACK",
               "entry_low": 1.1159, "entry_high": 1.1161, "stop": 1.1150}
        sample = {"frames": {"M15": {"last_3_completed_candles": [
            {"time": "2026-10-27T09:15:00Z",
             "mid": {"o": 1.1160, "h": 1.1166, "l": 1.1149, "c": 1.1164}}
        ]}}}
        self.assertIsNone(tracker._forward_entry_pattern(
            row, sample, tracker.moment("2026-10-27T09:31:00Z")))

    def test_prospective_m5_continuation_is_paper_only(self):
        row = {"first_seen_at": Q0, "expires_at": "2026-10-27T10:00:00Z",
               "direction": "SHORT", "route": "MOMENTUM_CONTINUATION",
               "entry_low": 1.1159, "entry_high": 1.1161, "stop": 1.1175}
        sample = {"frames": {"M5": {"last_3_completed_candles": [
            {"time": "2026-10-27T09:10:00Z",
             "mid": {"o": 1.11630, "h": 1.11645, "l": 1.11620, "c": 1.11628}},
            {"time": "2026-10-27T09:15:00Z",
             "mid": {"o": 1.11630, "h": 1.11631, "l": 1.11580, "c": 1.11585}}
        ]}}}
        observed = tracker._forward_entry_pattern(
            row, sample, tracker.moment("2026-10-27T09:21:00Z"))
        self.assertEqual(observed["entry_pattern_status"],
                         "FORWARD_CANDLE_OBSERVED_PAPER_ONLY")
        self.assertEqual(observed["entry_pattern_timeframe"], "M5")
        self.assertEqual(observed["entry_pattern_at"], "2026-10-27T09:20:00+00:00")

    def test_stale_quote_and_old_m15_do_not_create_early_signal(self):
        with patch.object(tracker, "build_research_setups", return_value=plan()):
            self.assertEqual(tracker.update_ledger(
                snapshot(quote_at="2026-10-27T08:50:00Z"), {})[
                    "total_registered"], 0)
            self.assertEqual(tracker.update_ledger(
                snapshot(at="2026-10-27T10:14:00Z",
                         quote_at="2026-10-27T10:13:00Z"), {})[
                    "total_registered"], 0)

    def test_research_only_public_summary_is_explicit_and_whitelisted(self):
        with patch.object(tracker, "build_research_setups", return_value=plan()):
            book = tracker.update_ledger(snapshot(), {})
        book["opportunities"][next(iter(book["opportunities"]))][
            "private_credential"] = "DO_NOT_PUBLISH"
        public = tracker.public_view(book)
        self.assertNotIn("DO_NOT_PUBLISH", repr(public))
        self.assertEqual(public["total_registered"], 1)
        self.assertIn("Hypothetical", public["disclaimer"])
        self.assertEqual(public["recent"][0]["entry_confirmed"], False)
        self.assertEqual(public["lifecycle_audit"]["forward_watches"], 1)
        self.assertEqual(public["lifecycle_audit"]["ever_meaningful_favourable"], 0)
        self.assertFalse(public["recent"][0]["ever_meaningful_favourable"])
        self.assertEqual(public["lifecycle_audit"]["verified_fills"], 0)

    def test_full_index_and_prospective_head_to_head_keep_every_denominator(self):
        shared = {"instrument": "EUR_USD", "direction": "LONG",
                  "first_seen_at": "2026-10-27T09:14:00Z",
                  "m15_impulse_started_at": "2026-10-27T09:00:00Z",
                  "entry_low": 1.1159, "entry_high": 1.1161,
                  "stop": 1.1145, "tp1": 1.1180,
                  "entry_confirmed": False, "scored": False}
        m5 = {**shared, "id": "m5", "route": "MOMENTUM_CONTINUATION",
              "entry_pattern_status": "FORWARD_CANDLE_OBSERVED_PAPER_ONLY",
              "entry_pattern_at": "2026-10-27T09:20:00Z",
              "tracking_status": "FORWARD_PATTERN_OBSERVED_NO_APPROVED_FILL"}
        m15 = {**shared, "id": "m15", "route": "EMA20_PULLBACK",
               "entry_pattern_status": "FORWARD_CANDLE_OBSERVED_PAPER_ONLY",
               "entry_pattern_at": "2026-10-27T09:30:00Z",
               "tracking_status": "FORWARD_PATTERN_OBSERVED_NO_APPROVED_FILL"}
        expired = {**shared, "id": "late", "route": "EMA20_PULLBACK",
                   "m15_impulse_started_at": "2026-10-27T10:00:00Z",
                   "entry_pattern_status": "AWAITING_INDEPENDENT_POST_PLAN_CANDLE",
                   "entry_pattern_at": None,
                   "tracking_status": "EXPIRED_RESEARCH_NO_VERIFIED_FILL"}
        result = tracker.public_view({"opportunities": {
            "m5": m5, "m15": m15, "late": expired}}, limit=1)
        self.assertEqual(len(result["recent"]), 1)
        self.assertEqual(len(result["candidate_index"]), 3)
        self.assertTrue(result["public_window"]["truncated"])
        self.assertEqual(result["route_study"]["MOMENTUM_CONTINUATION"]["median_pattern_delay_minutes"], 6)
        self.assertEqual(result["route_study"]["EMA20_PULLBACK"]["expired_without_pattern"], 1)
        self.assertEqual(result["head_to_head"]["paired_cohorts"], 1)
        self.assertEqual(result["head_to_head"]["m5_earlier"], 1)
        self.assertFalse(result["route_study"]["MOMENTUM_CONTINUATION"]["validated_trading_edge"])


    def test_late_hourly_review_uses_full_saved_m15_history_without_backdating(self):
        setup = [{**plan()[0], "direction": "LONG",
                  "entry_route": "EMA20_PULLBACK",
                  "entry_low": 1.1159, "entry_high": 1.1161,
                  "stop": 1.1145, "tp1": 1.1180}]
        with patch.object(tracker, "build_research_setups", return_value=setup):
            book = tracker.update_ledger(snapshot(), {})
        first = next(iter(book["opportunities"].values()))
        opening = "2026-10-27T09:30:00Z"
        target = {"time": opening, "mid": {
            "o": 1.11600, "h": 1.11642, "l": 1.11594, "c": 1.11630}}
        later = [{"time": f"2026-10-27T10:{minute:02d}:00Z",
                  "mid": {"o": 1.11625, "h": 1.11660,
                          "l": 1.11620, "c": 1.11640}}
                 for minute in (0, 15, 30)]
        full = snapshot("2026-10-27T10:46:00Z",
                        "2026-10-27T10:45:30Z", bid=1.1163, ask=1.1164)
        # Normal summary's last three bars have lost the 09:30 pattern.
        full["pairs"]["EUR_USD"]["frames"]["M15"]["last_3_completed_candles"] = later
        latest = copy.deepcopy(full)
        latest["pairs"]["EUR_USD"]["frames"]["M15"]["recent_completed_candles"] = [
            target, {"time": "2026-10-27T09:45:00Z",
                     "mid": {"o": 1.1163, "h": 1.1166, "l": 1.1161, "c": 1.1164}},
            *later]
        with patch.object(tracker, "build_research_setups", return_value=[]):
            future = tracker.update_ledger(full, book, latest=latest)
        row = next(iter(future["opportunities"].values()))
        self.assertEqual(row["entry_pattern_status"],
                         "FORWARD_CANDLE_OBSERVED_PAPER_ONLY")
        self.assertEqual(row["entry_pattern_at"], "2026-10-27T09:45:00+00:00")
        self.assertEqual(row["entry_pattern_detected_at"],
                         "2026-10-27T10:46:00+00:00")
        self.assertFalse(row["entry_confirmed"])
        self.assertFalse(row["scored"])
        self.assertFalse(row["broker_verified"])
        self.assertEqual(row["first_seen_at"], first["first_seen_at"])

    def test_mismatched_saved_candles_cannot_prove_a_forward_pattern(self):
        setup = [{**plan()[0], "direction": "LONG",
                  "entry_route": "EMA20_PULLBACK",
                  "entry_low": 1.1159, "entry_high": 1.1161,
                  "stop": 1.1145, "tp1": 1.1180}]
        with patch.object(tracker, "build_research_setups", return_value=setup):
            book = tracker.update_ledger(snapshot(), {})
        full = snapshot("2026-10-27T10:46:00Z", "2026-10-27T10:45:30Z")
        latest = copy.deepcopy(full)
        latest["pairs"]["EUR_USD"]["quote"]["time"] = "2026-10-27T10:00:00Z"
        latest["pairs"]["EUR_USD"]["frames"]["M15"]["recent_completed_candles"] = [
            {"time": "2026-10-27T09:30:00Z",
             "mid": {"o": 1.1160, "h": 1.11642,
                     "l": 1.11594, "c": 1.11630}}]
        with patch.object(tracker, "build_research_setups", return_value=[]):
            future = tracker.update_ledger(full, book, latest=latest)
        row = next(iter(future["opportunities"].values()))
        self.assertEqual(row["entry_pattern_status"],
                         "AWAITING_INDEPENDENT_POST_PLAN_CANDLE")
        self.assertFalse(row["entry_confirmed"])



if __name__ == "__main__":
    unittest.main()

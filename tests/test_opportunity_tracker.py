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

        with patch.object(tracker, "build_research_setups", return_value=[]):
            expired = tracker.update_ledger(snapshot(
                "2026-10-27T11:14:00Z", "2026-10-27T11:13:00Z",
                bid=1.1139, ask=1.1140), future)
        self.assertEqual(next(iter(expired["opportunities"].values()))[
            "tracking_status"], "EXPIRED_RESEARCH_NO_VERIFIED_FILL")

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


if __name__ == "__main__":
    unittest.main()

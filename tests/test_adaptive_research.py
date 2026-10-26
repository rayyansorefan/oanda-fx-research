"""Adaptive FX research is prospective and cannot back-label paper wins."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from adaptive_research import entry_context, afternoon_audit


def observed_pair():
    return {
        "quote": {"bid": 1.0999, "ask": 1.1001, "tradeable": True},
        "frames": {
            "M15": {
                "indicators": {
                    "ema20": 1.1000, "atr14": .0008,
                    "plus_di14": 28, "minus_di14": 13,
                    "macd_12_26_9": {"histogram": .0001},
                },
                "recent_completed_candles": [
                    {"time": "2026-10-26T15:00:00Z", "mid": {"o": 1.0990, "c": 1.0995}},
                    {"time": "2026-10-26T15:15:00Z", "mid": {"o": 1.0995, "c": 1.1000}},
                ],
            },
            "H1": {"indicators": {"plus_di14": 25, "minus_di14": 14, "adx14": 27}},
        },
    }


def ledger_item(created_at, changes, status="UNTESTABLE"):
    return {
        "plan": {"created_at": created_at, "instrument": "EUR_USD", "direction": "SHORT"},
        "result": {"status": status, "scored": False},
        "tracking": {
            "reference_kind": "ORIGINAL_PLANNED_ENTRY",
            "history": [
                {"at": when, "move_pct": move} for when, move in changes
            ],
        },
    }


class ProspectiveTechnicalTests(unittest.TestCase):
    def test_aligned_pair_is_not_arbitrarily_blocked(self):
        self.assertEqual(entry_context(observed_pair(), "LONG")["status"],
                         "NO_NEW_TECHNICAL_WARNING")

    def test_m15_dmi_and_macd_reversal_blocks_fresh_entry(self):
        pair = observed_pair()
        pair["frames"]["M15"]["indicators"].update(
            plus_di14=11, minus_di14=27,
            macd_12_26_9={"histogram": -.00015})
        output = entry_context(pair, "LONG")
        self.assertEqual(output["status"], "REASSESS_REVERSAL")
        self.assertTrue(output["block_new_alert"])

    def test_completed_m15_bodies_plus_opposing_dmi_block(self):
        pair = observed_pair()
        pair["frames"]["M15"]["indicators"].update(plus_di14=11, minus_di14=27)
        pair["frames"]["M15"]["recent_completed_candles"] = [
            {"time": "2026-10-26T15:00:00Z", "mid": {"o": 1.1003, "c": 1.1001}},
            {"time": "2026-10-26T15:15:00Z", "mid": {"o": 1.1002, "c": 1.0998}},
        ]
        self.assertEqual(entry_context(pair, "LONG")["status"], "REASSESS_REVERSAL")

    def test_overextended_price_blocks_chasing(self):
        pair = observed_pair()
        pair["quote"].update(bid=1.1020, ask=1.1022)
        output = entry_context(pair, "LONG")
        self.assertEqual(output["status"], "REASSESS_EXTENDED")
        self.assertGreater(output["m15_ema_distance_atr"], 1.55)

    def test_missing_indicators_is_never_clearance(self):
        pair = observed_pair()
        pair["frames"]["H1"]["indicators"].pop("minus_di14")
        self.assertTrue(entry_context(pair, "LONG")["block_new_alert"])
        self.assertEqual(entry_context(pair, "LONG")["status"], "UNVERIFIABLE")


class AfternoonAuditTests(unittest.TestCase):
    def test_includes_every_afternoon_registration_but_no_fake_wins(self):
        book = {"candidates": {
            "before": ledger_item("2026-10-26T14:55:00Z", []),
            "adverse": ledger_item("2026-10-26T15:10:00Z", [
                ("2026-10-26T15:20:00Z", 0),
                ("2026-10-26T16:20:00Z", -.14),
            ]),
            "positive": ledger_item("2026-10-26T15:30:00Z", [
                ("2026-10-26T15:35:00Z", 0),
                ("2026-10-26T17:35:00Z", .07),
            ]),
            "new": ledger_item("2026-10-26T17:39:00Z", [
                ("2026-10-26T17:39:20Z", 0),
            ]),
            "missing": ledger_item("2026-10-26T16:00:00Z", []),
        }}
        report = afternoon_audit(book, datetime(2026, 10, 26, 18, tzinfo=timezone.utc))
        self.assertEqual(report["registered"], 4)
        self.assertEqual([report[k] for k in ("adverse", "favourable", "insufficient", "missing")],
                         [1, 1, 1, 1])
        self.assertEqual(report["challenger"], "PAPER_ONLY_NOT_VALIDATED")
        self.assertNotIn("wins", report)
        self.assertEqual({r["id"] for r in report["candidates"]},
                         {"adverse", "positive", "new", "missing"})

    def test_overnight_retains_last_afternoon_not_empty_new_day(self):
        book = {"candidates": {"x": ledger_item("2026-10-26T15:30:00Z", [
            ("2026-10-26T15:40:00Z", 0),
            ("2026-10-26T17:40:00Z", -.1),
        ])}}
        report = afternoon_audit(book, "2026-10-27T05:00:00Z")
        self.assertEqual(report["session_date_uk"], "2026-10-26")
        self.assertEqual(report["adverse"], 1)


if __name__ == "__main__":
    unittest.main()

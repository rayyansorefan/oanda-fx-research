"""Fail-closed regression checks for scheduled ChatGPT signal handoff."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import signal_watch as watch

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)


def quote():
    return {
        "quote": {"bid": 1.33790, "ask": 1.33800,
                  "spread_pips": 1.0, "pip_size": .0001,
                  "tradeable": True, "time": NOW.isoformat()},
        "frames": {"M15": {"recent_completed_candles": []},
                   "H1": {"indicators": {}}}
    }


def good():
    return {"id": "20261021-GBPUSD-01", "instrument": "GBP_USD",
            "direction": "LONG",
            "created_at": (NOW - timedelta(minutes=5)).isoformat(),
            "expires_at": (NOW + timedelta(hours=3)).isoformat(),
            "planned_entry": 1.3378, "stop": 1.3358, "tp1": 1.3428,
            "tp2": 1.3448,
            "alert_b": 1.3376,
            "max_oanda_spread_pips": 1.3,
            "event_blackouts_utc": []}


class MonitorGateTests(unittest.TestCase):
    def test_well_structured_cost_viable_signal_passes(self):
        self.assertIsNone(watch.validate_approved_plan(good(), quote(), NOW))

    def test_real_preopen_bad_keys_are_rejected(self):
        bad = good()
        for canonical, alias in (
            ("stop", "stop_loss"), ("tp1", "take_profit_1"),
            ("tp2", "take_profit_2"),
            ("max_oanda_spread_pips", "max_spread_pips")):
            bad[alias] = bad.pop(canonical)
        self.assertIn("MISSING_FIELDS", watch.validate_approved_plan(bad, quote(), NOW))

    def test_actual_eurgbp_preopen_low_cost_adjusted_rr_is_rejected(self):
        plan = good()
        plan.update({"id": "20261021-EURGBP-S-01", "instrument": "EUR_GBP",
                     "direction": "SHORT", "planned_entry": 0.8397,
                     "stop": .84045, "tp1": .83848, "tp2": .83745,
                     "alert_b": .8396, "max_oanda_spread_pips": 1.5})
        q = quote()
        q["quote"].update({"bid": .8396, "ask": .8397,
                           "spread_pips": 1.0})
        self.assertEqual(watch.validate_approved_plan(plan, q, NOW),
                         "TP1_RR_BELOW_MINIMUM_AFTER_COSTS")

    def test_actual_gbpusd_preopen_gross_rr_is_rejected(self):
        plan = good()
        plan.update({"planned_entry": 1.3387,
                     "stop": 1.33715, "tp1": 1.34085,
                     "tp2": 1.3434, "alert_a": 1.3386,
                     "max_oanda_spread_pips": 1.3})
        self.assertEqual(watch.validate_approved_plan(plan, quote(), NOW),
                         "TP1_RR_BELOW_MINIMUM_AFTER_COSTS")

    def test_stale_quote_is_not_clear(self):
        q = quote()
        q["quote"]["time"] = (NOW - timedelta(minutes=30)).isoformat()
        self.assertEqual(watch.validate_approved_plan(good(), q, NOW),
                         "STALE_PAIR_QUOTE")

    def test_too_high_tp2_invalid(self):
        plan = good()
        plan["tp2"] = plan["tp1"] - .0001
        self.assertEqual(watch.validate_approved_plan(plan, quote(), NOW),
                         "INVALID_TP2_DIRECTION")

    def test_signal_without_entry_route_is_withheld(self):
        plan = good()
        plan.pop("alert_b")
        self.assertEqual(watch.validate_approved_plan(plan, quote(), NOW),
                         "NO_MONITORABLE_ENTRY_ROUTE")

    def test_missing_pair_data_never_qualifies(self):
        self.assertEqual(watch.validate_approved_plan(good(), None, NOW),
                         "PAIR_QUOTE_UNAVAILABLE")

    def test_malformed_signal_does_not_crash_all_ideas(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            snap, news, signals, state, report = [d / n for n in
                ("latest.json", "news.json", "signals.json", "state.json", "report.json")]
            snap.write_text(json.dumps({"status": "OK",
                                        "collected_at": NOW.isoformat(),
                                        "pairs": {"GBP_USD": quote()}}))
            news.write_text(json.dumps({"status": "PARTIAL",
                                        "collected_at": NOW.isoformat()}))
            bad = good()
            bad["stop_loss"] = bad.pop("stop")
            signals.write_text(json.dumps({"signals": [bad, good()]}))
            paths = {"SNAPSHOT": snap, "NEWS": news, "SIGNALS": signals,
                     "STATE": state, "ASSESSMENT": report}
            with patch.multiple(watch, **paths), patch.object(watch, "utc_now", return_value=NOW):
                watch.watch()
            saved = json.loads(report.read_text())
            self.assertEqual(saved["status"], "OK")
            self.assertEqual(saved["signals"][0]["status"], "INVALID_SETUP")
            self.assertEqual(saved["signals"][1]["status"],
                             "PAIR_M15_CANDLES_UNAVAILABLE")
            self.assertEqual(saved["counts"]["watchlist"], 2)



    def test_qualified_price_event_does_not_depend_on_github_issues(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            snap, news, signals, state, report = [d / n for n in
                ("latest.json", "news.json", "signals.json", "state.json", "report.json")]
            pair = quote()
            pair["frames"]["M15"]["recent_completed_candles"] = [
                {"time": (NOW - timedelta(minutes=15)).isoformat(),
                 "mid": {"o": 1.3378, "h": 1.3382, "l": 1.3375, "c": 1.3379}}
            ]
            snap.write_text(json.dumps({"status": "OK", "collected_at": NOW.isoformat(),
                                        "pairs": {"GBP_USD": pair}}))
            news.write_text(json.dumps({"status": "OK", "collected_at": NOW.isoformat()}))
            signals.write_text(json.dumps({"signals": [good()]}))
            paths = {"SNAPSHOT": snap, "NEWS": news, "SIGNALS": signals,
                     "STATE": state, "ASSESSMENT": report}
            with patch.multiple(watch, **paths), \
                 patch.object(watch, "utc_now", return_value=NOW), \
                 patch.object(watch, "news_check", return_value=(
                     "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES", [], [])), \
                 patch.object(watch, "setup_already_breached", return_value=None), \
                 patch.object(watch, "signal_match", return_value=(
                     "A", (NOW-timedelta(minutes=15)).isoformat(), "M15 test event")), \
                 patch.object(watch, "notify", side_effect=AssertionError("No GitHub Issues")), \
                 patch.object(watch, "news_alert", side_effect=AssertionError("No GitHub Issues")):
                watch.watch()
            data = json.loads(report.read_text())
            self.assertEqual(data["signals"][0]["status"], "PRICE_REASSESSMENT_ALERT_READY")
            self.assertEqual(data["counts"]["price_alerts"], 1)
            saved_state = json.loads(state.read_text())
            self.assertIn("A", saved_state["setups"][good()["id"]]["notified"])

if __name__ == "__main__":
    unittest.main()

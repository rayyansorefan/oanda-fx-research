"""Synthetic forward-only gate ablation: no broker trades, no retrospective fill."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts import gate_forward_research as study


def t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


PROTOCOL = {
    "id": "FX-GATE-20261106-v1", "no_orders": True,
    "prospective_start_at": "2026-11-06T10:30:00Z", "expiry_minutes": 90,
}


def sample(at="2026-11-06T10:46:00Z", news="PRIMARY_NEWS_SOURCE_MISSING"):
    now = t(at)
    row = {
        "pair": "AUD_SGD", "direction": "SHORT", "route": "EMA20_PULLBACK",
        "failed": ["news_gate"], "news_reason": news,
        "checks": {"news_gate": False, "trend_H1": True, "relative_spread_cap": True,
                   "gross_reward_minimum": True, "net_reward_minimum": True},
        "economic_feasibility_only": {
            "entry_reference": 0.88149, "stop_reference": 0.88242,
            "nearest_target": 0.87912, "gross_rr": 2.53, "net_rr": 1.78
        },
    }
    summary = {
        "status": "OK", "collected_at": at,
        "pairs": {"AUD_SGD": {
            "quote": {"time": at, "bid": 0.88148, "ask": 0.88150,
                      "spread_pips": 0.2, "pip_size": .0001, "tradeable": True},
            "frames": {"M15": {"completed_at": "2026-11-06T10:30:00Z"}}
        }}
    }
    raw = {"status": "DIAGNOSTICS_ONLY", "source_at": at, "rows": [row]}
    return raw, summary


class ProspectiveGateStudy(unittest.TestCase):
    def test_first_run_only_activates_then_new_source_registers(self):
        raw, summary = sample()
        first = study.update({}, raw, summary, {}, PROTOCOL, now=t("2026-11-06T10:46:10Z"))
        self.assertEqual(first["summary"]["registered"], 0)
        self.assertEqual(first["activated_at"], "2026-11-06T10:46:10Z")
        next_raw, next_summary = sample("2026-11-06T11:01:00Z")
        second = study.update(first, next_raw, next_summary, {}, PROTOCOL,
                              now=t("2026-11-06T11:01:10Z"),
                              fetch_bars=lambda *_: [])
        self.assertEqual(second["summary"]["registered"], 1)
        row = next(iter(second["records"].values()))
        self.assertTrue(row["no_live_entry"])
        self.assertEqual(row["result"]["status"], "PENDING_TRIGGER")
        self.assertEqual(row["plan"]["created_at"], "2026-11-06T11:01:10Z")
        self.assertEqual(row["plan"]["strategy"],
                         "PROSPECTIVE_NEWS_COVERAGE_ABLATION_PAPER")

    def test_old_source_cannot_register_after_first_activation(self):
        raw, summary = sample("2026-11-06T10:46:00Z")
        book = {"protocol_id": PROTOCOL["id"], "activated_at": "2026-11-06T10:50:00Z"}
        result = study.update(book, raw, summary, {}, PROTOCOL, now=t("2026-11-06T10:51:00Z"))
        self.assertEqual(result["summary"]["registered"], 0)

    def test_known_adverse_news_is_never_waived(self):
        raw, summary = sample("2026-11-06T11:01:00Z", news="RECENT_MATERIAL_HEADLINE_REVIEW")
        row = study.candidate(raw["rows"][0], summary, t(raw["source_at"]),
                              t("2026-11-06T11:01:10Z"), PROTOCOL)
        self.assertIsNone(row)

    def test_multiple_failed_gates_not_in_experiment(self):
        raw, summary = sample()
        raw["rows"][0]["failed"].append("trend_H1")
        self.assertIsNone(study.candidate(raw["rows"][0], summary, t(raw["source_at"]),
                                          t("2026-11-06T10:46:10Z"), PROTOCOL))

    def test_unverified_missing_candles_remains_gap_not_profit(self):
        raw, summary = sample()
        rec = study.candidate(raw["rows"][0], summary, t(raw["source_at"]),
                              t("2026-11-06T10:46:10Z"), PROTOCOL)
        assert rec
        result = study.advance(rec, summary, {}, t("2026-11-06T11:10:00Z"),
                               fetch_bars=lambda *_: [])
        self.assertEqual(result["result"]["status"], "DATA_GAP")
        self.assertFalse(result["result"]["scored"])

    def test_api_failure_cannot_become_loss_or_win(self):
        raw, summary = sample()
        rec = study.candidate(raw["rows"][0], summary, t(raw["source_at"]),
                              t("2026-11-06T10:46:10Z"), PROTOCOL)
        assert rec
        def fail(*args):
            raise RuntimeError("OANDA_HTTP_401_CANDLES")
        result = study.advance(rec, summary, {}, t("2026-11-06T11:01:00Z"),
                               fetch_bars=fail)
        self.assertEqual(result["result"]["status"], "PENDING_TRIGGER")
        self.assertEqual(result["replay_attempts"][-1]["error"], "OANDA_HTTP_401_CANDLES")

    def test_pair_observation_clocks_only(self):
        summary = sample()[1]
        history = {"observations": [
            {"at": "2026-11-06T10:50:00Z", "pairs": ["EUR_USD"]},
            {"at": "2026-11-06T10:52:00Z", "pairs": ["AUD_SGD"]},
            {"at": "2026-11-06T10:52:00Z", "pairs": ["AUD_SGD"]},
            {"at": "2026-11-06T11:15:00Z", "pairs": ["AUD_SGD"]},
        ]}
        obs = study.observation_times("AUD_SGD", t("2026-11-06T10:46:10Z"),
                                      t("2026-11-06T11:00:00Z"), summary, history)
        self.assertEqual(obs, [t("2026-11-06T10:52:00Z")])

    def test_repeated_same_day_source_does_not_rewrite_plan(self):
        raw, summary = sample("2026-11-06T11:01:00Z")
        initial = {"protocol_id": PROTOCOL["id"],
                   "activated_at": "2026-11-06T10:46:10Z"}
        first = study.update(initial, raw, summary, {}, PROTOCOL,
                             now=t("2026-11-06T11:01:10Z"))
        rec = copy.deepcopy(next(iter(first["records"].values())))
        later_raw, later_summary = sample("2026-11-06T12:01:00Z")
        second = study.update(first, later_raw, later_summary, {}, PROTOCOL,
                              now=t("2026-11-06T12:01:10Z"),
                              fetch_bars=lambda *_: [])
        self.assertEqual(second["summary"]["registered"], 1)
        self.assertEqual(next(iter(second["records"].values()))["plan"], rec["plan"])

    def test_disabled_protocol_rejected(self):
        raw, summary = sample()
        with self.assertRaisesRegex(ValueError, "INVALID_GATE_ABLATION_PROTOCOL"):
            study.update({}, raw, summary, {}, {**PROTOCOL, "no_orders": False},
                         now=t("2026-11-06T11:00:00Z"))


if __name__ == "__main__":
    unittest.main()

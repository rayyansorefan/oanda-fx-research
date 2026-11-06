"""Conservative, auditable FX strategy feedback. Research only; no orders.

Pepperstone screenshots are execution observations, not automatically scored
signals. Only immutable OANDA paper replays enter the statistical research bias,
and only after the established minimum sample and conservative bounds.
"""
from __future__ import annotations

import math
import json
from datetime import datetime, timezone
from pathlib import Path
from paper_trade_audit import public_view

ROOT = Path(__file__).resolve().parents[1]
WATCH_LOSS_CLASSES = frozenset({
    "UNAPPROVED_RESEARCH_WATCH_EXECUTED", "USER_EXECUTED_AMBER_RESEARCH_WATCH"
})


def build_feedback(book, broker):
    view = public_view(book if isinstance(book, dict) else {})
    rows = (broker or {}).get("trades") or []
    closed = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "CLOSED":
            continue
        pnl = row.get("net_pnl_gbp")
        if isinstance(pnl, bool) or not isinstance(pnl, (int, float)) or not math.isfinite(pnl):
            continue
        # A unique screenshot close is one observation. Reimport must not
        # inflate wins/losses or falsely imply more statistical evidence.
        key = row.get("key")
        if isinstance(key, str) and key:
            if key in seen:
                continue
            seen.add(key)
        closed.append(row)
    # A claimed match alone is not sufficient to train: confirmation, route,
    # source signal and fills must be independently audited first.
    unlinked = sum(row.get("signal_match") != "VERIFIED" or
                   not row.get("signal_id") for row in closed)
    groups = view.get("strategy") or {}
    # Broker evidence informs execution-quality review, never the paper model's
    # expectancy or its parameter weights without independently verified linkage.
    pair_observations = {}
    for row in closed:
        instrument = row.get("instrument")
        if not isinstance(instrument, str):
            continue
        group = pair_observations.setdefault(instrument, {
            "closed": 0, "wins": 0, "losses": 0,
            "net_pnl_gbp": 0.0, "unapproved_watch_losses": 0,
            "early_manual_wins": 0, "early_manual_losses": 0,
            "unconfirmed_entry_stopouts": 0, "risk_budget_breaches": 0,
            "positive_review_wins": 0})
        group["closed"] += 1
        group["wins"] += row["net_pnl_gbp"] > 0
        group["losses"] += row["net_pnl_gbp"] < 0
        group["net_pnl_gbp"] += row["net_pnl_gbp"]
        group["unapproved_watch_losses"] += (
            row["net_pnl_gbp"] < 0
            and row.get("entry_classification") in WATCH_LOSS_CLASSES)
        early = row.get("entry_classification") == "USER_EXECUTED_EARLY_BEFORE_M15_CONFIRMATION"
        group["early_manual_wins"] += early and row["net_pnl_gbp"] > 0
        group["early_manual_losses"] += early and row["net_pnl_gbp"] < 0
        group["unconfirmed_entry_stopouts"] += (
            row["net_pnl_gbp"] < 0 and
            row.get("entry_classification") == "USER_DISCRETIONARY_BEFORE_M15_CONFIRMATION"
            and row.get("exit_reason") == "STOP_LOSS")
        group["risk_budget_breaches"] += (
            (row.get("execution_review") or {}).get("risk_above_target") is True)
        group["positive_review_wins"] += (
            row["net_pnl_gbp"] > 0 and
            (row.get("signal_match") != "VERIFIED" or not row.get("signal_id")))
    for group in pair_observations.values():
        group["net_pnl_gbp"] = round(group["net_pnl_gbp"], 2)
    watch_losses = [row for row in closed
                    if row["net_pnl_gbp"] < 0
                    and row.get("entry_classification") in WATCH_LOSS_CLASSES]
    early = [row for row in closed if row.get("entry_classification")
             == "USER_EXECUTED_EARLY_BEFORE_M15_CONFIRMATION"]
    # Broker wins are useful positive hypotheses for investigation; none
    # are scored as successful research strategies until signal lineage is audited.
    positive_reviews = [
        {"trade_key": row.get("key"), "instrument": row.get("instrument"),
         "net_gbp": row["net_pnl_gbp"], "signal_match": row.get("signal_match"),
         "weight_change": 0,
         "next_step": "Compare frozen prospective route against losses, costs, missed entries and no-fills."}
        for row in closed if row["net_pnl_gbp"] > 0
        and (row.get("signal_match") != "VERIFIED" or not row.get("signal_id"))
    ]
    return {
        "basis": "OANDA bid/ask paper outcomes, separately from manual Pepperstone screenshots",
        "paper_updated_at": view.get("updated_at"),
        "paper_totals": view.get("totals", {}),
        "paper_strategy": groups,
        "broker": {
            "closed_count": len(closed),
            "profitable_count": sum(row["net_pnl_gbp"] > 0 for row in closed),
            "loss_count": sum(row["net_pnl_gbp"] < 0 for row in closed),
            "win_rate_pct": round(100 * sum(row["net_pnl_gbp"] > 0 for row in closed) / len(closed), 1) if closed else None,
            "pair_observations": pair_observations,
            "positive_review_queue": positive_reviews[-20:],
            "confirmed_strategy_wins_from_broker_screenshots": 0,
            "unapproved_watch_stopouts": len(watch_losses),
            "early_entry_study": {
                "closed": len(early),
                "wins": sum(row["net_pnl_gbp"] > 0 for row in early),
                "losses": sum(row["net_pnl_gbp"] < 0 for row in early),
                "net_gbp": round(sum(row["net_pnl_gbp"] for row in early), 2),
                "strict_plan_retouch_not_observed": sum(
                    (row.get("execution_review") or {}).get(
                    "standard_planned_price_retouch_observed_in_oanda_bid_bars") is False
                    for row in early),
                "cases": [{"pair": row.get("instrument"),
                           "plan": row.get("related_research_plan_id"),
                           "net_gbp": row["net_pnl_gbp"],
                           "signal_match": row.get("signal_match")}
                          for row in early[-20:]],
                "policy": "Compare a completed M5 alternative against the original M15 route prospectively. Count losses, no-fills and costs. Broker screenshots never auto-approve entries."
            },
            "execution_lessons": [
                {
                    "instrument": row.get("instrument"),
                    "closed_at": row.get("closed_at"),
                    "classification": row.get("entry_classification"),
                    "planned_entry_deviation_pips": (row.get("execution_review") or {}).get("entry_deviation_pips"),
                    "research_gate": (row.get("execution_review") or {}).get("original_risk_gate"),
                    "action": "Treat watch-only candidates as NOT APPROVED; require a fresh independently confirmed entry."
                } for row in watch_losses[-5:]
            ],
            "net_profit_gbp": round(sum(row["net_pnl_gbp"] for row in closed), 2),
            "unlinked_count": unlinked,
            "included_in_strategy_weighting": 0,
            "policy": "No broker screenshots train strategy weights until independently linked to original immutable signal, trigger, fill and exit."
        },
        "threshold": "Paper bias neutral until >=30 scored samples, >=10 days, >=3 pairs and conservative 99% mean-R bound excludes zero. Never relax execution, spread or news gates."
    }


def signal_policy(signal, feedback):
    """Select prior paper route evidence only for the actual proposed routes."""
    if not isinstance(signal, dict):
        return {"priority_bias": 0, "status": "INVALID_SIGNAL"}
    strategy = signal.get("strategy") or (
        "M15_BREAKOUT_RETEST" if signal.get("alert_a") is not None
        and signal.get("alert_b") is None else "M15_REJECTION")
    routes = [route for key, route in
              (("alert_a", "A"), ("alert_b", "B"), ("alert_c", "C"))
              if signal.get(key) is not None]
    records = [(route, (feedback.get("paper_strategy") or {}).get(
        str(strategy) + " / ROUTE_" + route)) for route in routes]
    supported = [r for _, r in records if isinstance(r, dict)]
    # Unknown route evidence cannot be treated as affirmative. A proven
    # negative on any viable entry route is a conservative alert veto.
    negative = any(r.get("paper_priority_bias") == -1 for r in supported)
    positive = (len(supported) == len(routes) and bool(routes)
                and all(r.get("paper_priority_bias") == 1 for r in supported))
    bias = -1 if negative else 1 if positive else 0
    # A known stop-out after executing a non-approved watch is an operational
    # review flag for the same pair, not proof that the direction is wrong.
    pair_audit = ((feedback.get("broker") or {}).get("pair_observations") or {}).get(
        signal.get("instrument"), {})
    watch_review = pair_audit.get("unapproved_watch_losses", 0) > 0
    early_stop_review = pair_audit.get("unconfirmed_entry_stopouts", 0) > 0
    budget_review = pair_audit.get("risk_budget_breaches", 0) > 0
    needs_execution_review = watch_review or early_stop_review or budget_review
    return {
        "strategy": str(strategy), "routes": routes, "priority_bias": bias,
        "status": ("HISTORICAL_PAPER_CAUTION" if bias < 0 else
                   "HISTORICAL_PAPER_SUPPORT_NOT_ENTRY_APPROVAL" if bias > 0 else
                   "NEUTRAL_INSUFFICIENT_OR_MIXED_EVIDENCE"),
        "scored_route_samples": {route: r.get("count", 0)
                                 for route, r in records if isinstance(r, dict)},
        "broker_observations_not_weighted": feedback.get("broker", {}).get("closed_count", 0),
        "manual_execution_review_required": needs_execution_review,
        "positive_broker_pattern_review_available": pair_audit.get("positive_review_wins", 0) > 0,
        "broker_positive_wins_not_weighted": pair_audit.get("positive_review_wins", 0),
        "early_entry_study_available": (pair_audit.get("early_manual_wins", 0) +
                                         pair_audit.get("early_manual_losses", 0)) > 0,
        "execution_review": (
            "Recent stop-loss followed execution of a non-approved paper watch on this pair. "
            "Recheck completed-candle trigger, spread-adjusted reward/risk, and trend conflict; "
            "do not assume this is a new entry approval."
            if watch_review else
            "A discretionary early entry stopped out or exceeded the risk budget. "
            "Require completed M15 confirmation, fresh spread/news check and <=0.25% "
            "risk against verified equity; do not turn a research watch into approval."
            if needs_execution_review else None),
    }


def main():
    """Refresh the PRIVATE manual-execution audit separately from OANDA scoring.

    Independent of live OANDA collection: an outage must not erase a verified
    screenshot stop-out or turn it into an OANDA strategy fill.
    """
    source = ROOT / "data/verified_broker_outcomes.json"
    book_path = ROOT / "data/paper_trade_ledger.json"
    broker = json.loads(source.read_text(encoding="utf-8"))
    paper_valid = book_path.is_file() and book_path.stat().st_size > 0
    paper = json.loads(book_path.read_text(encoding="utf-8")) if paper_valid else {}
    report = build_feedback(paper, broker)
    report["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report["source"] = "PRIVATE_USER_SCREENSHOT_EVIDENCED_BROKER_OUTCOMES"
    report["paper_input_status"] = "PRESENT" if paper_valid else "MISSING_OR_EMPTY"
    report["model_weight_update"] = "NONE"
    report["alert_gate_changes"] = "NONE"
    output = ROOT / "data/manual_execution_review.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                      encoding="utf-8")
    print("Private manual execution review: closed", report["broker"]["closed_count"],
          "watch losses", report["broker"]["unapproved_watch_stopouts"],
          "weighted", report["broker"]["included_in_strategy_weighting"])


if __name__ == "__main__":
    main()

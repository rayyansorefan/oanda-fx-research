"""Conservative, auditable FX strategy feedback. Research only; no orders.

Pepperstone screenshots are execution observations, not automatically scored
signals. Only immutable OANDA paper replays enter the statistical research bias,
and only after the established minimum sample and conservative bounds.
"""
from __future__ import annotations

import math
from paper_trade_audit import public_view


def build_feedback(book, broker):
    view = public_view(book if isinstance(book, dict) else {})
    rows = (broker or {}).get("trades") or []
    closed = [row for row in rows if isinstance(row, dict)
              and row.get("status") == "CLOSED"
              and isinstance(row.get("net_pnl_gbp"), (int, float))
              and math.isfinite(row["net_pnl_gbp"])]
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
            "early_manual_wins": 0, "early_manual_losses": 0})
        group["closed"] += 1
        group["wins"] += row["net_pnl_gbp"] > 0
        group["losses"] += row["net_pnl_gbp"] < 0
        group["net_pnl_gbp"] += row["net_pnl_gbp"]
        group["unapproved_watch_losses"] += (
            row["net_pnl_gbp"] < 0
            and row.get("entry_classification") == "UNAPPROVED_RESEARCH_WATCH_EXECUTED")
        early = row.get("entry_classification") == "USER_EXECUTED_EARLY_BEFORE_M15_CONFIRMATION"
        group["early_manual_wins"] += early and row["net_pnl_gbp"] > 0
        group["early_manual_losses"] += early and row["net_pnl_gbp"] < 0
    for group in pair_observations.values():
        group["net_pnl_gbp"] = round(group["net_pnl_gbp"], 2)
    watch_losses = [row for row in closed
                    if row["net_pnl_gbp"] < 0
                    and row.get("entry_classification") == "UNAPPROVED_RESEARCH_WATCH_EXECUTED"]
    early = [row for row in closed if row.get("entry_classification")
             == "USER_EXECUTED_EARLY_BEFORE_M15_CONFIRMATION"]
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
    needs_execution_review = pair_audit.get("unapproved_watch_losses", 0) > 0
    return {
        "strategy": str(strategy), "routes": routes, "priority_bias": bias,
        "status": ("HISTORICAL_PAPER_CAUTION" if bias < 0 else
                   "HISTORICAL_PAPER_SUPPORT_NOT_ENTRY_APPROVAL" if bias > 0 else
                   "NEUTRAL_INSUFFICIENT_OR_MIXED_EVIDENCE"),
        "scored_route_samples": {route: r.get("count", 0)
                                 for route, r in records if isinstance(r, dict)},
        "broker_observations_not_weighted": feedback.get("broker", {}).get("closed_count", 0),
        "manual_execution_review_required": needs_execution_review,
        "early_entry_study_available": (pair_audit.get("early_manual_wins", 0) +
                                         pair_audit.get("early_manual_losses", 0)) > 0,
        "execution_review": (
            "Recent stop-loss followed execution of a non-approved paper watch on this pair. "
            "Recheck completed-candle trigger, spread-adjusted reward/risk, and trend conflict; "
            "do not assume this is a new entry approval."
            if needs_execution_review else None),
    }

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
    return {
        "basis": "OANDA bid/ask paper outcomes, separately from manual Pepperstone screenshots",
        "paper_updated_at": view.get("updated_at"),
        "paper_totals": view.get("totals", {}),
        "paper_strategy": groups,
        "broker": {
            "closed_count": len(closed),
            "profitable_count": sum(row["net_pnl_gbp"] > 0 for row in closed),
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
    return {
        "strategy": str(strategy), "routes": routes, "priority_bias": bias,
        "status": ("HISTORICAL_PAPER_CAUTION" if bias < 0 else
                   "HISTORICAL_PAPER_SUPPORT_NOT_ENTRY_APPROVAL" if bias > 0 else
                   "NEUTRAL_INSUFFICIENT_OR_MIXED_EVIDENCE"),
        "scored_route_samples": {route: r.get("count", 0)
                                 for route, r in records if isinstance(r, dict)},
        "broker_observations_not_weighted": feedback.get("broker", {}).get("closed_count", 0)
    }

"""Research-only prospective portfolio/cost audit of already recorded OANDA paper entries.

This does NOT create fills, broker costs, position orders or strategy approvals.
Historical and new holdout observations remain separate. R is a per-position
initial-risk proxy, not a cash account return. Broker swap/commission are unknown.
"""
from __future__ import annotations
import json
import math
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/paper_portfolio_audit.json"
ARMS = ("champion", "challenger_v1")
TERMINAL = {"WIN_TP1", "LOSS_STOP", "TIME_EXIT"}
SLIPPAGE_PIPS_PER_SIDE = (0.0, 0.25, 0.5)
# Stress probes, not verified Pepperstone pricing or universal swap values.
COMMISSION_PROXY_PIPS_PER_SIDE = 0.15
FUNDING_PROXY_PIPS_PER_OVERNIGHT = 0.25
MAX_TOTAL_OPEN = 3
MAX_PER_CURRENCY = 2


def time(value):
    try:
        x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return x.astimezone(timezone.utc) if x.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def pip(pair):
    return 0.01 if pair.endswith("_JPY") else 0.0001


def same_currency(a, b):
    return bool(set(a.split("_")) & set(b.split("_")))


def proxy_rollovers(entry, exit_at):
    """Calendar sensitivity only, not a verified financing debit.

    A full NY 17:00 rollover model would also require currency-specific,
    day-specific broker swap rates and triple-swap day conventions.
    """
    if not entry or not exit_at or exit_at < entry:
        return None
    # Conservative date-boundary proxy, explicitly NOT actual 17:00 rollover.
    return max(0, (exit_at.date() - entry.date()).days)


def report(study, protocol, observed_at):
    start = time(protocol.get("holdout_start_at"))
    now = time(observed_at)
    if not start or not now:
        raise ValueError("Explicit UTC holdout and observation dates required")
    arms = {}
    for policy in ARMS:
        records = [r for r in study.get("records", []) if
                   isinstance(r, dict) and r.get("policy") == policy and
                   isinstance(r.get("plan"), dict)]
        selected = []
        exclusions = Counter()
        for r in records:
            p, result = r["plan"], r.get("result") or {}
            if p.get("instrument") == "GBP_ZAR":
                exclusions["EXCLUDED_PAIR"] += 1
                continue
            created = time(p.get("created_at"))
            entered = time(result.get("entry_at"))
            if not created or created < start:
                exclusions["HISTORICAL_NOT_FORWARD"] += 1
                continue
            if entered is None:
                exclusions["UNFILLED"] += 1
                continue
            if entered < created:
                exclusions["PRECREATION_FILL"] += 1
                continue
            if entered > now:
                exclusions["FUTURE_FILL_NOT_YET_OBSERVED"] += 1
                continue
            symbol = p.get("instrument", "")
            entry = num(result.get("entry"))
            stop = num(p.get("stop"))
            if not isinstance(symbol, str) or len(symbol) != 7 or symbol[3] != "_" or entry is None or stop is None or entry == stop:
                exclusions["INVALID_OR_MISSING_ECONOMICS"] += 1
                continue
            closed = time(result.get("at"))
            status = result.get("status")
            if closed is not None and closed > now:
                exclusions["FUTURE_EXIT_NOT_YET_OBSERVED"] += 1
                continue
            if status in TERMINAL:
                if closed is None or closed < entered or result.get("scored") is not True or num(result.get("net_r")) is None:
                    exclusions["UNSCORED_OR_UNDATED_EXIT"] += 1
                    continue
            elif status != "OPEN":
                exclusions["AMBIGUOUS_OR_UNVERIFIABLE"] += 1
                continue
            selected.append((entered, closed, p, result))
        selected.sort(key=lambda t: (t[0], t[2].get("instrument", ""), str(t[2].get("id", ""))))
        accepted = []
        overlaps = []
        for entered, closed, p, result in selected:
            active = [x for x in accepted if x["entry_at"] <= entered and (x["exit_at"] is None or x["exit_at"] > entered)]
            pair = p["instrument"]
            currencies = pair.split("_")
            blocked = (
                "TOTAL_CONCURRENT_LIMIT" if len(active) >= MAX_TOTAL_OPEN else
                "DUPLICATE_OPEN_PAIR" if any(x["pair"] == pair for x in active) else
                "CURRENCY_CONCENTRATION" if any(sum(currency in x["pair"].split("_") for x in active) >= MAX_PER_CURRENCY for currency in currencies) else
                None
            )
            if blocked:
                overlaps.append({"plan_id": str(p.get("id", "")), "pair": pair, "entry_at": entered.isoformat(), "reason": blocked})
                continue
            entry, stop = num(result["entry"]), num(p["stop"])
            risk_pips = abs(entry - stop) / (num(p.get("pip_size")) or pip(pair))
            if risk_pips <= 0:
                exclusions["INVALID_RISK_PIPS"] += 1
                continue
            status = result.get("status")
            total_r = num(result.get("net_r")) if status in TERMINAL else None
            rollovers = proxy_rollovers(entered, closed) if closed else None
            scenarios = {}
            for slip in SLIPPAGE_PIPS_PER_SIDE:
                # OANDA bid/ask execution is already in replay R; do NOT subtract spread again.
                extra_pips = 2 * slip + 2 * COMMISSION_PROXY_PIPS_PER_SIDE
                if rollovers is not None:
                    extra_pips += rollovers * FUNDING_PROXY_PIPS_PER_OVERNIGHT
                scenarios[format(slip, "g")] = round(total_r-extra_pips/risk_pips, 6) if total_r is not None and rollovers is not None else None
            accepted.append({"plan_id": str(p.get("id", "")), "pair": pair,
                             "entry_at": entered, "exit_at": closed,
                             "status": status, "base_oanda_r": total_r,
                             "scenario_r": scenarios, "risk_pips": risk_pips,
                             "overnight_proxy_count": rollovers})
        scored = [x for x in accepted if x["base_oanda_r"] is not None]
        ordered = sorted(scored, key=lambda x: (x["exit_at"], x["entry_at"], x["plan_id"]))
        stressed = [x["scenario_r"]["0.5"] for x in ordered]
        equity = peak = drawdown = 0.0
        for r in stressed:
            if r is None:
                continue
            equity += r
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        open_rows = [x for x in accepted if x["exit_at"] is None and x["entry_at"] <= now]
        arms[policy] = {
            "forward_candidates_evaluated": len(records)-exclusions["HISTORICAL_NOT_FORWARD"],
            "eligible_paper_fills_after_portfolio_limits": len(accepted),
            "independently_scored_exits": len(scored),
            "open_paper_positions": len(open_rows),
            "open_exposure_pairs": sorted({x["pair"] for x in open_rows}),
            "blocked_overlapping_entries": len(overlaps),
            "overlap_reasons": dict(Counter(x["reason"] for x in overlaps)),
            "excluded": dict(exclusions),
            "total_oanda_bidask_r": round(sum(x["base_oanda_r"] for x in scored), 6) if scored else None,
            "cost_stress_total_r": {k: round(sum(x["scenario_r"][k] for x in scored),6) if scored else None for k in ("0","0.25","0.5")},
            "max_cost_stressed_drawdown_r": round(drawdown,6) if scored else None,
            "cost_scenarios_complete": bool(scored) and all(v is not None for x in scored for v in x["scenario_r"].values()),
        }
    return {"schema": 1, "checked_at": now.isoformat(),
            "protocol_id": protocol["id"], "holdout_start_at": protocol["holdout_start_at"],
            "policy": {"max_total_open": MAX_TOTAL_OPEN, "max_per_currency": MAX_PER_CURRENCY,
                       "slippage_pips_per_side": list(SLIPPAGE_PIPS_PER_SIDE),
                       "commission_proxy_pips_per_side": COMMISSION_PROXY_PIPS_PER_SIDE,
                       "funding_proxy_pips_per_calendar_overnight": FUNDING_PROXY_PIPS_PER_OVERNIGHT,
                       "source": "Already-confirmed OANDA paper bid/ask fills; no invented entry",
                       "cost_limit": "Commission/swap are deliberately UNVERIFIED illustrative stress proxies, NOT Pepperstone net P&L",
                       "exposure_limit": "Sequential shadow portfolio, not an order rejection or changed original experiment"},
            "arms": arms, "broker_costs_verified": False,
            "benchmark": {"no_trade_r": 0.0, "description": "Cash/no-trade opportunity-cost reference only; no predictive edge benchmark established"},
            "performance_validated": False, "promoted": False,
            "status": "FORWARD_RESEARCH_ONLY" if now >= start else "HOLDOUT_NOT_STARTED"}


def main():
    study = json.loads((ROOT / "data/strategy_comparison.json").read_text())
    protocol = json.loads((ROOT / "validation_protocol.json").read_text())
    outcome = report(study, protocol, datetime.now(timezone.utc).isoformat())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(outcome, sort_keys=True, indent=2) + "\n")
    print("FX paper portfolio research-only",
          {k: (v["independently_scored_exits"], v["open_paper_positions"], v["blocked_overlapping_entries"]) for k, v in outcome["arms"].items()})


if __name__ == "__main__":
    main()

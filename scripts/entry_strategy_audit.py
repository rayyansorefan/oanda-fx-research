"""Independent, read-only FX gate and prospective strategy audit.

Runs in the existing hourly workflow. No orders, new schedules or promotion.
Existing canonical plans and validation protocols are never modified.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from scripts.forward_validation import moment, stress_result, bootstrap_days
except ModuleNotFoundError:
    from forward_validation import moment, stress_result, bootstrap_days

ROOT = Path(__file__).resolve().parents[1]
LONDON = ZoneInfo("Europe/London")
# One-factor ablations are diagnostics, NOT deployable admission policies.
ABLATIONS = {
    "champion_all_gates": frozenset(),
    "without_m15_adx_paper": frozenset({"adx_M15"}),
    "without_daily_ema_paper": frozenset({"trend_D"}),
    "without_reversal_veto_paper": frozenset({"no_reversal_or_exhaustion"}),
    "without_news_gate_diagnostic_only": frozenset({"news_gate"}),
    "without_nearest_target_economics_diagnostic_only":
        frozenset({"gross_reward_minimum", "net_reward_minimum"}),
}
ARMS = ("champion", "challenger_v1")


def load(path, default):
    p = ROOT / path
    return json.loads(p.read_text()) if p.exists() else default


def write(path, value):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def gate_analysis(raw):
    rows = [r for r in raw.get("rows") or [] if isinstance(r, dict)]
    out = {}
    for name, relaxed in ABLATIONS.items():
        eligible = []
        unknown = 0
        for r in rows:
            checks = r.get("checks") or {}
            remaining = [v for k, v in checks.items() if k not in relaxed]
            if not remaining or any(v is None for v in remaining):
                unknown += 1
                continue
            if all(v is True for v in remaining):
                eligible.append(r["pair"] + "|" + r["route"])
        out[name] = {
            "counterfactual_pair_routes": len(set(eligible)),
            "unverifiable": unknown,
            "approval_allowed": name == "champion_all_gates",
            "test_scope": "SAME_SNAPSHOT_FILTER_DIAGNOSTIC_NOT_FORWARDED_FILLS",
        }
    isolated = {}
    for key in sorted({key for row in rows for key in (row.get("checks") or {})}):
        isolated[key] = sum(
            (r.get("checks") or {}).get(key) is False
            and all(v is True for k, v in (r.get("checks") or {}).items() if k != key)
            for r in rows
        )
    return {
        "source_at": raw.get("source_at"),
        "route_observations": len(rows),
        "distinct_pairs": len({r.get("pair") for r in rows}),
        "counterfactual_arms": out,
        "isolated_only_blocker": isolated,
        "failures": {key: sum((r.get("checks") or {}).get(key) is False for r in rows)
                     for key in sorted(isolated)},
        "limitations": [
            "The same market snapshot does not measure predictive value or profitability.",
            "Ablations that waive the news check or minimum reward/risk are diagnostic only and never yield alerts.",
            "Pair-route observations share prices and currency exposure; they are not independent trade outcomes.",
        ],
    }


def _uk_day(when):
    return when.astimezone(LONDON).date().isoformat()


def _priced_result(row, stress_pips):
    result = row.get("result") or {}
    if result.get("scored") is not True or not moment(result.get("entry_at")):
        return None
    outcome = stress_result(row["plan"], result, stress_pips)
    if not outcome or outcome.get("status") != "OANDA_COST_SCENARIO":
        return None
    value = outcome.get("net_r")
    return value if isinstance(value, (float, int)) and math.isfinite(value) else None


def _dedup_exposure(rows):
    """Chronological, first-per-pair/day and non-overlapping currencies/day."""
    chosen = []
    occupied_by_day = defaultdict(set)
    seen_pair_days = set()
    ordered = sorted(rows, key=lambda r: ((r.get("result") or {}).get("entry_at") or "",
                                         (r.get("plan") or {}).get("id") or ""))
    for row in ordered:
        plan = row["plan"]
        entry = moment((row.get("result") or {}).get("entry_at"))
        if not entry:
            continue
        day, pair = _uk_day(entry), plan["instrument"]
        key = (day, pair)
        currencies = set(pair.split("_"))
        if key in seen_pair_days or occupied_by_day[day] & currencies:
            continue
        seen_pair_days.add(key)
        occupied_by_day[day].update(currencies)
        chosen.append(row)
    return chosen


def validation_assessment(study, protocol, delivery, review=None, now=None):
    """Paired observation days include zero-return champion abstention.

    This is not trade optimisation. Eligibility is pre-registered before any
    net outcome is considered. Actual broker/delivery evidence is separate.
    """
    now = now or datetime.now(timezone.utc)
    start = moment(protocol.get("holdout_start_at"))
    if start is None:
        raise ValueError("Missing immutable holdout start")
    observations = {arm: set() for arm in ARMS}
    candidates = {arm: [] for arm in ARMS}
    excluded = Counter()
    for row in study.get("records") or []:
        if not isinstance(row, dict) or row.get("policy") not in ARMS:
            continue
        decision = moment(row.get("decision_at"))
        if not decision or decision < start:
            excluded["BEFORE_REGISTERED_HOLDOUT"] += 1
            continue
        if row.get("observation_scope") != "PAIR_SPECIFIC":
            excluded["NON_PAIR_SPECIFIC_TIMING"] += 1
            continue
        if row.get("common_deep_history_available") is not True:
            excluded["COMMON_FRAME_HISTORY_MISSING"] += 1
            continue
        pair = row.get("pair")
        if not pair or pair == "GBP_ZAR":
            continue
        arm = row["policy"]
        day = _uk_day(decision)
        observations[arm].add((pair, day))
        plan = row.get("plan")
        if plan and moment(plan.get("created_at")) and moment(plan["created_at"]) >= start:
            candidates[arm].append(row)
    common = observations["champion"] & observations["challenger_v1"]
    days = sorted({day for _, day in common})
    stress = max(protocol.get("slippage_pips_per_side") or [0.5])
    scored = {}
    arms = {}
    all_cost_evidenced = True
    all_delivery_evidenced = True
    events = (delivery or {}).get("events") or {}
    for arm in ARMS:
        rows = [r for r in candidates[arm] if (r["pair"], _uk_day(moment(r["decision_at"]))) in common]
        raw = [r for r in rows if (r.get("result") or {}).get("scored") is True]
        independent = _dedup_exposure(raw)
        scored[arm] = independent
        values = {}
        unpriced = 0
        for row in independent:
            val = _priced_result(row, stress)
            if val is None:
                unpriced += 1
                all_cost_evidenced = False
                continue
            day = _uk_day(moment(row["result"]["entry_at"]))
            values[day] = values.get(day, 0.0) + val
            result = row["result"]
            if result.get("broker_costs_verified") is not True:
                all_cost_evidenced = False
            received = False
            for event in events.values():
                if event.get("plan_id") != row["plan"]["id"]:
                    continue
                when = moment(event.get("verified_user_delivery_at"))
                entry = moment(result.get("entry_at"))
                if (event.get("verification_kind") in ("PROVIDER_DELIVERED_RECEIPT", "USER_ACKNOWLEDGEMENT")
                    and event.get("verification_source_reference") and when and entry
                    and moment(event.get("detected_at")) and
                    moment(event["detected_at"]) <= when and when + timedelta(minutes=1) <= entry):
                    received = True
            if not received:
                all_delivery_evidenced = False
        arms[arm] = {
            "eligible_pair_days": len(observations[arm] & common),
            "plans_created": len(rows),
            "scored_unique_currency_exposure_days": len(independent),
            "stress_cost_unverifiable": unpriced,
            "outcomes": dict(Counter((r.get("result") or {}).get("status", "NOT_REPLAYED") for r in rows)),
            "sum_net_r_stressed": round(sum(values.values()), 5) if values else None,
            "abstaining_common_days": len(days) - len(values),
        }
        arms[arm]["_day_net"] = values
    differences = [arms["challenger_v1"]["_day_net"].get(day, 0.0) -
                   arms["champion"]["_day_net"].get(day, 0.0) for day in days]
    ci = bootstrap_days(differences, protocol.get("bootstrap_resamples", 10000)) if len(days) >= 2 else None
    for arm in ARMS:
        del arms[arm]["_day_net"]
    # These sample floors come from v8's registered protocol. The champion
    # need not fill a trade: it can abstain on each shared observation day.
    floor_days = int(protocol.get("minimum_scored_days_per_arm", 20))
    floor_challenger = int(protocol.get("minimum_independent_scored_pair_days_per_arm", 100))
    enough = len(days) >= floor_days and len(scored["challenger_v1"]) >= floor_challenger
    has_cost = bool(scored["challenger_v1"]) and all_cost_evidenced
    has_delivery = bool(scored["challenger_v1"]) and all_delivery_evidenced
    cohort_digest = hashlib.sha256(json.dumps({
        "protocol": protocol.get("id"), "holdout": protocol.get("holdout_start_at"),
        "common_days": days, "arms": arms, "paired_interval": ci
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    approval = (review or {}).get("independent_reviewer_approval")
    reference = (review or {}).get("source_reference")
    independent_review = (approval is True and isinstance(reference, str)
                          and len(reference) >= 12 and (review or {}).get("reviewer")
                          and (review or {}).get("cohort_sha256") == cohort_digest)
    cost_scenarios_complete = all(arms[a]["stress_cost_unverifiable"] == 0 for a in ARMS)
    if not enough or ci is None:
        decision, reason = "INSUFFICIENT_EVIDENCE", "Registered observation-day or challenger sample floor not reached"
    elif not cost_scenarios_complete:
        decision, reason = "INSUFFICIENT_EVIDENCE", "Some scored outcome costs or chronology cannot be reconstructed"
    elif ci["upper_95pct_r"] < 0:
        decision, reason = "FAIL", "Day-clustered stressed challenger improvement is below zero, including champion abstention"
    elif ci["lower_95pct_r"] > 0 and has_cost and has_delivery and independent_review:
        decision, reason = "PASS", "Positive stressed paired-day interval and independently evidenced execution availability"
    else:
        decision, reason = "INSUFFICIENT_EVIDENCE", "Statistical superiority or required broker, receipt and independent-review evidence missing"
    return {
        "updated_at": now.isoformat(), "protocol_id": protocol.get("id"),
        "registered_holdout_start_unchanged": protocol["holdout_start_at"],
        "decision": decision, "reason": reason,
        "trading_performance_validated": decision == "PASS",
        "automatic_promotion_allowed": False,
        "common_eligible_pair_days": len(common), "common_eligible_observation_days": len(days),
        "earliest_common_day": days[0] if days else None,
        "excluded": dict(excluded), "arms": arms,
        "paired_day_cluster_interval": ci, "cohort_sha256": cohort_digest,
        "sample_floors": {"common_days": floor_days,
                          "challenger_currency_exposure_days": floor_challenger,
                          "champion_fills_required": 0},
        "evidence": {"broker_costs_complete": has_cost,
                     "user_receipts_before_entry_complete": has_delivery,
                     "independent_review_attested": independent_review,
                     "independent_reviewer": (review or {}).get("reviewer") if independent_review else None,
                     "scored_cost_scenarios_complete": cost_scenarios_complete},
        "caution": "Research-only counterfactual portfolio proxy. Missing broker costs or receipts prevent demonstrated manual achievability. No orders or parameter promotion.",
    }


def main():
    gate = gate_analysis(load("data/admission_full_gate_audit.json", {}))
    log = load("data/entry_gate_ablation_history.json", {"schema": 1, "snapshots": []})
    if gate["source_at"] and all(r.get("source_at") != gate["source_at"] for r in log["snapshots"]):
        log["snapshots"].append(gate)
    write("data/entry_gate_ablation_history.json", log)
    write("data/entry_gate_ablation_latest.json", gate)
    report = validation_assessment(
        load("data/strategy_comparison.json", {}),
        load("validation_protocol.json", {}),
        load("data/signal_delivery_audit.json", {}),
        load("data/independent_review_signoff.json", {}))
    write("data/entry_strategy_validation.json", report)
    print("Independent FX research audit:", gate.get("source_at"), report["decision"],
          "common days", report["common_eligible_observation_days"],
          "champion plans", report["arms"]["champion"]["plans_created"],
          "challenger plans", report["arms"]["challenger_v1"]["plans_created"])


if __name__ == "__main__":
    main()

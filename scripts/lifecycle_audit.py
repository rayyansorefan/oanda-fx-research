"""Evidence-only lifecycle reconciliation of existing FX plans.

This module does not change strategy rules, plan admission, execution prices,
notifications, alerts or broker positions. Its output is PRIVATE research
evidence. It deliberately does not count a ranked observation as a trade.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOSED_RESULTS = {"WIN_TP1", "LOSS_STOP", "TIME_EXIT"}
AMBIGUOUS_RESULTS = {"AMBIGUOUS_ENTRY_BAR", "AMBIGUOUS_EXIT_BAR",
                     "DATA_GAP", "UNTESTABLE", "SOURCE_UNAVAILABLE"}
VALID_STATES = {"WATCH", "QUALIFIED_PENDING_TRIGGER", "TRIGGERED", "PAPER_OPEN",
                "CLOSED", "EXPIRED_UNFILLED", "INVALIDATED", "UNVERIFIABLE"}


def timestamp(value):
    if not value:
        return None
    try:
        v = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return v.astimezone(timezone.utc) if v.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def state_for(result, expires_at, observed_at, *, canonical_plan=False, trigger_observed=False):
    """Entry expiry does not close an already-filled position."""
    result = result or {}
    status = result.get("status")
    if status in CLOSED_RESULTS:
        return "CLOSED"
    if status == "OPEN" or result.get("status") == "PAPER_OPEN":
        return "PAPER_OPEN"
    if status in AMBIGUOUS_RESULTS:
        return "UNVERIFIABLE"
    if status == "INVALIDATED_PRE_ENTRY" or status == "INVALIDATED":
        return "INVALIDATED"
    expired = bool(timestamp(expires_at) and observed_at and observed_at >= timestamp(expires_at))
    if status == "UNTRIGGERED":
        return "EXPIRED_UNFILLED" if expired else "QUALIFIED_PENDING_TRIGGER"
    if status == "TRIGGERED_UNFILLED" or trigger_observed:
        return "EXPIRED_UNFILLED" if expired else "TRIGGERED"
    if expired:
        return "UNVERIFIABLE" if canonical_plan else "EXPIRED_UNFILLED"
    # An actually frozen paper plan is still a plan, not an unpriced WATCH.
    return "QUALIFIED_PENDING_TRIGGER"


def event_key(policy, plan_id, status, result):
    """A repeated replay of a fixed outcome cannot mint another fill or exit."""
    evidence = {"policy": policy, "plan_id": plan_id, "status": status,
                "triggered_at": result.get("triggered_at"),
                "entry_at": result.get("entry_at"), "exit_at": result.get("at"),
                "outcome": result.get("status"), "entry": result.get("entry"),
                "exit": result.get("exit"), "net_r": result.get("net_r")}
    return digest(evidence)[:32]



def economic_identity(plan):
    """Economic equivalence only: UTC timestamp spellings and numeric serialisation may differ.

    Never use an ID alone to attach a fill, stop or TP to a frozen manual plan.
    A route/trigger difference can represent a completely different execution.
    """
    if not isinstance(plan, dict):
        return None
    keys = ("instrument", "direction", "entry_route", "created_at", "expires_at",
            "planned_entry", "entry_low", "entry_high", "stop", "tp1", "tp2",
            "alert_a", "alert_b", "alert_c", "max_oanda_spread_pips",
            "event_blackouts_utc")
    if not all(plan.get(k) is not None for k in
               ("instrument", "direction", "created_at", "expires_at",
                "planned_entry", "stop", "tp1")):
        return None
    out = {}
    for key in keys:
        value = plan.get(key)
        if key in ("created_at", "expires_at"):
            dt = timestamp(value)
            if dt is None:
                return None
            value = dt.isoformat()
        elif key in ("planned_entry", "entry_low", "entry_high", "stop",
                     "tp1", "tp2", "alert_a", "alert_b", "alert_c",
                     "max_oanda_spread_pips") and value is not None:
            try:
                value = round(float(value), 10)
            except (TypeError, ValueError, OverflowError):
                return None
        elif key == "event_blackouts_utc":
            value = value or []
        out[key] = value
    return canonical(out)


def match_paper_result(plan, index):
    """Return result only if the matched economic evidence is noncontradictory."""
    rows = index.get(str(plan["id"]), [])
    if not rows:
        return {}, None
    identity = economic_identity(plan)
    compatible = [row for row in rows if identity is not None and
                  economic_identity(row.get("plan")) == identity]
    if not compatible:
        return {}, "PAPER_ECONOMICS_MISMATCH:" + str(plan["id"])
    results = [row.get("result") or {} for row in compatible]
    distinct = {canonical(r) for r in results}
    if len(distinct) > 1:
        return {}, "CONTRADICTORY_PAPER_OUTCOMES:" + str(plan["id"])
    return results[0], (
        "PAPER_ECONOMICS_CONFLICTING_ID:" + str(plan["id"])
        if len(compatible) != len(rows) else None
    )


def source_lifecycle(summary, comparison, signals, archive, fast_events, opportunities,
                     previous=None, *, assessed_at=None, holdout_start_at=None, paper_book=None):
    """Reconcile distinct immutable plans, with no snapshot-derived duplicate wins."""
    now = timestamp(assessed_at) if assessed_at else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("Assessment timestamp must be explicit UTC")
    source_at = summary.get("collected_at")
    source = timestamp(source_at)
    if source is None:
        raise ValueError("No independently dated OANDA source")
    previous = previous or {"schema": 1, "plans": {}, "events": {},
                            "source_at": None, "issues": []}
    original = canonical(previous)
    book = json.loads(original)  # cannot mutate caller's ledger
    book.setdefault("schema", 1)
    book.setdefault("plans", {})
    book.setdefault("events", {})
    book.setdefault("issues", [])
    prior = timestamp(book.get("source_at"))
    if prior and source < prior:
        # Stale concurrent runner: keep newer ledger bit-for-bit.
        return previous
    if now < source:
        raise ValueError("Assessment precedes source timestamp")
    holdout = timestamp(holdout_start_at)
    fast_by_plan = {}
    for item in (fast_events.get("events") or {}).values():
        if isinstance(item, dict) and item.get("plan_id") and timestamp(item.get("first_detected_at")):
            if timestamp(item.get("first_detected_at")) >= timestamp(item.get("plan_created_at")) if timestamp(item.get("plan_created_at")) else False:
                fast_by_plan.setdefault(str(item["plan_id"]), []).append(item)

    paper_index = defaultdict(list)
    for row in ((paper_book or {}).get("candidates") or {}).values():
        if isinstance(row, dict) and isinstance(row.get("plan"), dict):
            identifier = row["plan"].get("id")
            if identifier:
                paper_index[str(identifier)].append(row)
    observations = comparison.get("records") or []
    sources = [
        ("champion_manual_plan", row, "champion", True)
        for row in (signals.get("signals") or []) + (archive.get("signals") or [])
        if isinstance(row, dict)
    ]
    # Strategy comparisons are independent paper cohorts; NEVER count them
    # as canonical manual signals or user-reported Pepperstone positions.
    sources.extend(
        ("paper_comparison", r.get("plan"), str(r.get("policy")), False, r)
        for r in observations if isinstance(r, dict) and isinstance(r.get("plan"), dict)
        and r.get("policy") in ("champion", "challenger_v1")
    )
    anomalies = set(book.get("issues") or [])
    for entry in sources:
        kind, plan, policy, is_canonical = entry[:4]
        comparison_row = entry[4] if len(entry) > 4 else {}
        identifier = plan.get("id")
        if not identifier or not timestamp(plan.get("created_at")) or not timestamp(plan.get("expires_at")):
            anomalies.add("MALFORMED_PLAN:" + str(identifier or "UNKNOWN")[:64])
            continue
        key = (("conditional:" if is_canonical else "paper:") + policy + ":" + identifier)
        frozen = digest(plan)
        existing = book["plans"].get(key)
        if existing is not None and existing["original_sha256"] != frozen:
            anomalies.add("IMMUTABLE_CONFLICT:" + key)
            continue  # Never replace original plan/levels.
        if is_canonical:
            result, paper_issue = match_paper_result(plan, paper_index)
            if paper_issue:
                anomalies.add(paper_issue)
        else:
            result = comparison_row.get("result") or {}
        detected = fast_by_plan.get(identifier) if is_canonical else None
        has_trigger = bool(detected) or bool(timestamp(result.get("triggered_at")))
        state = state_for(result, plan["expires_at"], now, canonical_plan=is_canonical,
                          trigger_observed=has_trigger)
        if existing is None:
            book["plans"][key] = {
                "key": key, "id": identifier, "source_kind": kind, "policy": policy,
                "evidence_type": "FROZEN_CONDITIONAL" if is_canonical else
                                 "FORWARD_EXPERIMENT" if holdout and
                                 timestamp(plan["created_at"]) >= holdout else "HISTORICAL_OR_DEVELOPMENT",
                "original_sha256": frozen, "original": plan, "current_state": state,
                "registered_at": plan["created_at"], "source_at": source_at,
                "last_observed_at": source_at, "result_status": result.get("status"),
                "scored": False,
            }
        rec = book["plans"][key]
        old_state = rec["current_state"]
        # A CLOSED or PAPER_OPEN execution cannot become unfilled just because
        # its *entry plan* expired; terminal evidence dominates a later outage.
        if old_state == "CLOSED" and state != "CLOSED":
            state = "CLOSED"
        elif old_state == "PAPER_OPEN" and state in ("EXPIRED_UNFILLED", "WATCH",
                                                     "QUALIFIED_PENDING_TRIGGER"):
            state = "PAPER_OPEN"
        rec["current_state"] = state
        rec["last_observed_at"] = source_at
        rec["result_status"] = result.get("status") or rec.get("result_status")
        rec["scored"] = bool(result.get("scored") is True) or rec.get("scored", False)
        if result.get("scored") is True and result.get("net_r") is not None:
            rec["net_r"] = result["net_r"]
        if has_trigger:
            rec["trigger_seen"] = True
        if old_state != state or existing is None:
            event = {"plan_key": key, "state": state, "result_status": result.get("status"),
                     "triggered_at": result.get("triggered_at"),
                     "entry_at": result.get("entry_at"), "exit_at": result.get("at"),
                     "source_at": source_at, "evidence_type": rec["evidence_type"]}
            k = event_key(policy, key, state, result)
            if k not in book["events"]:
                book["events"][k] = event
    # Watches are observations, not frozen price-admitted plans.
    raw_watches = [v for v in (opportunities.get("opportunities") or {}).values()
                   if isinstance(v, dict) and v.get("instrument") != "GBP_ZAR"]
    seen_watch_ids = {str(row.get("id")) for row in raw_watches if row.get("id")}
    counts = Counter(row["current_state"] for row in book["plans"].values())
    strategies = {}
    for arm in ("champion", "challenger_v1"):
        sample = [p for p in book["plans"].values() if p["source_kind"] == "paper_comparison" and p["policy"] == arm]
        strategies[arm] = {"plans": len(sample),
                           "historical_or_development": sum(p["evidence_type"] == "HISTORICAL_OR_DEVELOPMENT" for p in sample),
                           "forward_experimental": sum(p["evidence_type"] == "FORWARD_EXPERIMENT" for p in sample),
                           "scored": sum(bool(p.get("scored")) for p in sample),
                           "triggered": sum(bool(p.get("trigger_seen")) for p in sample),
                           "states": dict(Counter(p["current_state"] for p in sample))}
    book["issues"] = sorted(anomalies)
    book["source_at"] = source_at
    book["counts"] = dict(counts)
    book["summary"] = {
        "ranked_observations": len(summary.get("ranked_review_candidates") or []),
        "distinct_unadmitted_watch_observations": len(seen_watch_ids),
        "conditional_frozen_plans": sum(p["source_kind"] == "champion_manual_plan" for p in book["plans"].values()),
        "paper_strategy_comparison": strategies,
        "manual_broker_trades_verified": 0,
        "verified_user_delivery_receipts": 0,
        "source_at": source_at,
        "not_independent_trades": True,
        "interpretation": "Unique frozen plans, not snapshot-level candidate rows. Only independently scored bid/ask exits enter a performance sample.",
    }
    if canonical(book) != original:
        book["updated_at"] = now.isoformat()
    return book


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".fx-ledger-",
                                         suffix=".tmp", delete=False, encoding="utf-8") as handle:
            name = handle.name
            handle.write(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def read(path, default):
    path = ROOT / path
    return json.loads(path.read_text()) if path.is_file() else default


def main():
    summary = read("data/summary.json", {})
    protocol = read("validation_protocol.json", {})
    led = source_lifecycle(
        summary, read("data/strategy_comparison.json", {}),
        read("signals.json", {}), read("data/signal_plan_archive.json", {}),
        read("data/fast_entry_events.json", {}), read("data/entry_opportunity_ledger.json", {}),
        read("data/lifecycle_ledger.json", {}),
        holdout_start_at=protocol.get("holdout_start_at"),
        paper_book=read("data/paper_trade_ledger.json", {}),
    )
    path = ROOT / "data/lifecycle_ledger.json"
    if not path.exists() or json.loads(path.read_text()) != led:
        atomic_json(path, led)
    print("FX lifecycle independent-plan audit", json.dumps(led.get("summary") or {}, sort_keys=True))


if __name__ == "__main__":
    main()


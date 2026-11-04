"""Fail-closed assessment health, independent of the OANDA raw-data collector.

A failed strategy test must never suppress collection or be labelled 'no trades'.
This module contains no order placement, broker access, or notification delivery.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ESSENTIAL = ("collect", "safety", "news", "admission", "watch", "comparison", "validation", "strategy_audit")
MAX_SOURCE_AGE_SECONDS = 70 * 60  # Existing hourly UI freshness policy, not a five-minute entry SLA.


def _utc(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None


def assess(summary, stages, *, now=None, workflow_run_id=None, workflow_sha=None):
    now = now or datetime.now(timezone.utc)
    source_at = summary.get("collected_at")
    source = _utc(source_at)
    age = (now - source).total_seconds() if source else None
    failures = [key.upper() + "_" + str(stages.get(key, "missing")).upper()
                for key in ESSENTIAL if stages.get(key) != "success"]
    if summary.get("status") not in ("OK", "PARTIAL"):
        failures.append("SOURCE_COLLECTION_INVALID")
    if age is None or age < -300 or age > MAX_SOURCE_AGE_SECONDS:
        failures.append("SOURCE_STALE_OR_INVALID")
    return {
        "schema": 1,
        "status": "ASSESSMENT_UNAVAILABLE" if failures else "AVAILABLE",
        "reason_codes": failures,
        "source_at": source_at,
        "assessed_at": now.isoformat().replace("+00:00", "Z"),
        "source_age_seconds": round(age, 1) if age is not None else None,
        "stage_outcomes": {name: stages.get(name, "missing") for name in ESSENTIAL},
        "workflow_run_id": str(workflow_run_id or ""),
        "workflow_sha": str(workflow_sha or ""),
        "user_delivery_verified": False,
        "no_orders": True,
    }


def apply_to_public_feed(feed, health, *, expected_source_at, now=None):
    """Remove *all* executable-looking routes on missing/failed/mismatched health.

    Keep original market timestamps and historical ledgers; never call a test
    failure a market rejection or forge a more recent quote.
    """
    now = now or datetime.now(timezone.utc)
    source = _utc(feed.get("snapshot_at"))
    age = (now - source).total_seconds() if source else None
    verified = (isinstance(health, dict) and health.get("status") == "AVAILABLE"
                and health.get("source_at") == expected_source_at
                and bool(_utc(health.get("assessed_at")))
                and age is not None and -300 <= age <= MAX_SOURCE_AGE_SECONDS)
    reasons = [] if verified else list((health or {}).get("reason_codes") or ["HEALTH_MISSING_OR_MISMATCHED"])
    view = {
        "status": "AVAILABLE" if verified else "ASSESSMENT_UNAVAILABLE",
        "reason_codes": reasons,
        "source_at": feed.get("snapshot_at"),
        "last_assessed_at": (health or {}).get("assessed_at"),
        "source_age_seconds_at_publication": round(age, 1) if age is not None else None,
        "validated_entry_trigger": False,
        "verified_user_delivery": False,
    }
    feed.setdefault("learning", {})["assessment_health"] = view
    if not verified:
        feed["status"] = "ASSESSMENT_UNAVAILABLE"
        feed["signals"] = []
        feed["research_setups"] = []
        feed.setdefault("hourly", {})["signals"] = []
        learning = feed["learning"]
        learning["current_scouts"] = []
        learning["entry_diagnostics"] = []
        if isinstance(feed.get("fast_entry_monitor"), dict):
            feed["fast_entry_monitor"]["actionable_count"] = 0
            feed["fast_entry_monitor"]["observations"] = []
    return feed


def main():
    parser = argparse.ArgumentParser()
    for name in ESSENTIAL:
        parser.add_argument("--" + name.replace("_", "-"), default="missing")
    args = parser.parse_args()
    try:
        summary = json.loads((ROOT / "data/summary.json").read_text())
    except (OSError, ValueError):
        summary = {}
    stages = {key: getattr(args, key) for key in ESSENTIAL}
    import os
    health = assess(summary, stages, workflow_run_id=os.environ.get("GITHUB_RUN_ID"),
                    workflow_sha=os.environ.get("GITHUB_SHA"))
    path = ROOT / "data/assessment_health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(health, indent=2, sort_keys=True) + "\n")
    print("FX assessment health", health["status"], health["reason_codes"])


if __name__ == "__main__":
    main()

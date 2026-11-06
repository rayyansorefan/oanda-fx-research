"""Evidence-based fast-dispatch cadence attestation, never a scheduler claim.

Only actual persisted fast-attempt receipts can establish observation cadence.
Github schedule/push jobs, a dispatch HTTP 204, stale snapshots, missing
pair-level quote evidence, or unverified scheduler origin cannot count.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
COMPLETE = "FAST_RESEARCH_CHECK_COMPLETE"
MIN_INTERVAL = 8 * 60
MAX_INTERVAL = 22 * 60


def moment(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def classify(row, now):
    if row.get("event") != "workflow_dispatch":
        return None  # push/schedule are not external-dispatch evidence
    checked = moment(row.get("checked_at"))
    source = moment(row.get("source_at"))
    result = {"run_id": str(row.get("run_id") or ""), "checked_at": row.get("checked_at"),
              "source_at": row.get("source_at"), "valid": False, "reason": None}
    if checked is None or checked > now + timedelta(minutes=1):
        result["reason"] = "INVALID_OR_FUTURE_ATTEMPT_CLOCK"
        return result
    result["_checked"] = checked
    local = checked.astimezone(LONDON)
    if local.weekday() >= 5 or not 7 <= local.hour < 23:
        result["reason"] = "OUTSIDE_ACTIVE_LONDON_RESEARCH_SESSION"
        return result
    if row.get("status") != COMPLETE or not isinstance(row.get("pair_evidence"), dict):
        result["reason"] = "FAILED_OR_MISSING_PAIR_EVIDENCE"
        return result
    if not row.get("run_id") or not str(row["run_id"]).isdigit():
        result["reason"] = "MISSING_RUN_ID"
        return result
    if source is None or not timedelta(0) <= checked - source <= timedelta(minutes=5):
        result["reason"] = "INVALID_SOURCE_CLOCK"
        return result
    if not isinstance(row.get("analysed"), int) or row["analysed"] < 1 or not row["pair_evidence"]:
        result["reason"] = "NO_ANALYSED_PAIRS"
        return result
    # At least one pair must have its own current OANDA quote and completed M5
    # and M15 bars. A generic source timestamp or receipt alone is insufficient.
    count = 0
    for name, pair in row["pair_evidence"].items():
        if not isinstance(name, str) or not isinstance(pair, dict):
            continue
        quote = moment(pair.get("quote_at"))
        bars = pair.get("completed_candle_open_times") or {}
        m5 = moment(bars.get("M5"))
        m15 = moment(bars.get("M15"))
        if (quote is not None and m5 is not None and m15 is not None
                and timedelta(0) <= source - quote <= timedelta(minutes=5)
                and m5 <= source and m15 <= source):
            count += 1
    result["fresh_pair_count"] = count
    if not count:
        result["reason"] = "NO_PAIR_SPECIFIC_FRESH_QUOTE_AND_CANDLES"
        return result
    result["valid"] = True
    result["reason"] = "PAIR_OBSERVATION_VERIFIED"
    return result


def summarize(directory, now=None):
    now = now or datetime.now(timezone.utc)
    directory = Path(directory)
    attempts = []
    for path in (sorted(directory.glob("*.json")) if directory.is_dir() else []):
        try:
            row = json.loads(path.read_text())
            event = classify(row, now)
            if event is not None:
                attempts.append(event)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    attempts.sort(key=lambda x: x.get("_checked") or datetime.min.replace(tzinfo=timezone.utc))
    # Collapse reruns by ID. Never count the same run twice, regardless of
    # attempt number or duplicated persisted receipts.
    unique = {}
    for row in attempts:
        if row["run_id"]:
            unique[row["run_id"]] = row
    ordered = sorted(unique.values(), key=lambda x: x.get("_checked") or datetime.min.replace(tzinfo=timezone.utc))
    streak = []
    best_streak = []
    sessions = set()
    for item in ordered:
        if not item["valid"]:
            streak = []
            continue
        if streak:
            previous = streak[-1]
            interval = (item["_checked"] - previous["_checked"]).total_seconds()
            if (not MIN_INTERVAL <= interval <= MAX_INTERVAL
                    or item["source_at"] == previous["source_at"]
                    or item["_checked"].astimezone(LONDON).date() != previous["_checked"].astimezone(LONDON).date()):
                streak = []
        streak.append(item)
        if len(streak) >= 4:
            sessions.add(item["_checked"].astimezone(LONDON).date().isoformat())
        if len(streak) > len(best_streak):
            best_streak = streak[:]
    latest_four = streak[-4:] if len(streak) >= 4 else []
    intervals = [(b["_checked"] - a["_checked"]).total_seconds()
                 for a, b in zip(latest_four, latest_four[1:])]
    summary = {
        "status": ("NO_DISPATCH_RECEIPTS" if not ordered else
                   "FOUR_EVENT_OBSERVATIONS_SEEN_ORIGIN_UNVERIFIED" if len(streak) >= 4 else
                   "INSUFFICIENT_OR_INTERRUPTED_EVENT_OBSERVATIONS"),
        "dispatch_attempt_receipts": len(ordered),
        "valid_new_market_observations": sum(r["valid"] for r in ordered),
        "latest_consecutive_valid_event_observations": len(streak),
        "longest_valid_event_streak": len(best_streak),
        "four_consecutive_event_observations": len(streak) >= 4,
        "last_four_run_ids": [r["run_id"] for r in latest_four],
        "last_four_source_times": [r["source_at"] for r in latest_four],
        "actual_intervals_seconds": intervals,
        "sessions_with_four_event_observations": sorted(sessions),
        "two_session_observation_evidence": len(sessions) >= 2,
        "external_scheduler_origin_verified": False,
        "sustained_reliability_verified": False,
        "verification_note": "workflow_dispatch may be manual; external scheduler origin/requests and missing provider calls cannot be verified from repository receipts alone. Four observed pair-specific events and two sessions are necessary, not sufficient, for sustained reliability.",
        "recent_failures": [
            {"run_id": r["run_id"], "reason": r["reason"]}
            for r in ordered if not r["valid"]
        ][-8:],
    }
    return summary

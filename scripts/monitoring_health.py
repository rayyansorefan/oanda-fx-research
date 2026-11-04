"""Operational clocks and bounded recovery; never changes frozen research rules.

Cron target slots are compared with actual observations, not invented dispatch
timestamps. A dormant GitHub scheduler cannot be repaired by this process.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LONDON = ZoneInfo("Europe/London")
FAST_OK = "FAST_RESEARCH_CHECK_COMPLETE"


def read(path, default=None):
    p = ROOT / path
    return json.loads(p.read_text()) if p.exists() else ({} if default is None else default)


def when(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def age(value, now):
    t = when(value)
    return (now-t).total_seconds() if t and t <= now else None


def active(now):
    local = now.astimezone(LONDON)
    return local.weekday() < 5 and 7 <= local.hour < 19


def slots(now):
    """Today's configured fast observation targets, with five-minute grace."""
    local = now.astimezone(LONDON)
    if local.weekday() >= 5:
        return []
    return [t.astimezone(timezone.utc) for h in range(7, 19)
            for m in (2, 17, 32, 47)
            if (t := local.replace(hour=h, minute=m, second=0, microsecond=0))
            + timedelta(minutes=5) <= local]


def fast_valid(audit, snapshot, now):
    seconds = age(audit.get("source_at"), now)
    return (audit.get("status") == FAST_OK and audit.get("analysed", 0) > 0
            and audit.get("source_at") == snapshot.get("collected_at")
            and bool(snapshot.get("pairs")) and seconds is not None and seconds <= 20*60)


def fast_if_due(now=None):
    now = now or datetime.now(timezone.utc)
    audit = read("data/fast_entry_audit.json")
    elapsed = age(audit.get("source_at"), now)
    if not active(now) or (fast_valid(audit, read("data/fast_latest.json"), now)
                           and elapsed < 12*60):
        print("Fast fallback not due; no extra market requests.")
        return 0
    # Exactly one inline attempt, serialized by the existing workflow group.
    # Uses the unchanged fast collector and all of its existing safety gates.
    result = subprocess.run([sys.executable, "scripts/fast_entry_scan.py"], cwd=ROOT)
    checked = datetime.now(timezone.utc)
    valid = fast_valid(read("data/fast_entry_audit.json"), read("data/fast_latest.json"), checked)
    if result.returncode or not valid:
        print("::error::Fast recovery produced no verified fresh market observation")
        return 1
    return 0


def github(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "fx-monitoring-health"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    repo = os.environ.get("GITHUB_REPOSITORY", "rayyansorefan/oanda-fx-research")
    with urlopen(Request("https://api.github.com/repos/"+repo+"/"+path, headers=headers), timeout=15) as r:
        return json.load(r)


def schedule_evidence(runs, path):
    rows = sorted((r for r in runs if r.get("event") == "schedule" and r.get("path") == path),
                  key=lambda r: r["created_at"])
    times = [when(r["created_at"]) for r in rows]
    return {"run_ids": [r["id"] for r in rows],
            "created_at": [r["created_at"] for r in rows],
            "interval_seconds": [(b-a).total_seconds() for a,b in zip(times,times[1:])],
            "conclusions": [r.get("conclusion") for r in rows],
            "note": "Run creation intervals only; exact intended dispatch identity is unavailable."}


def build(now=None, remote=True):
    now = now or datetime.now(timezone.utc)
    summary = read("data/summary.json")
    fast = read("data/fast_entry_audit.json")
    latest = read("data/fast_latest.json")
    early = read("data/early_simple_paper_shadow.json")
    full_age = age(summary.get("collected_at"), now)
    fast_age = age(fast.get("source_at"), now)
    full_ok = summary.get("status") in ("OK", "PARTIAL") and full_age is not None and full_age <= 75*60
    fast_ok = fast_valid(fast, latest, now)
    history = read("data/fast_entry_history.json", {"observations": []})
    observed = [when(r.get("at")) for r in history.get("observations", []) if r.get("pairs")]
    # This is an observation continuity measurement, NOT proof an event was dropped.
    target_slots = slots(now)
    # One observation can satisfy at most one slot; overlapping grace windows
    # must never disguise a missed interval as two successful observations.
    all_targets = slots(now + timedelta(minutes=5))
    covered = set()
    for o in observed:
        eligible = [t for t in all_targets if o and t <= o <= t+timedelta(minutes=20)]
        if eligible:
            covered.add(max(eligible))
    missing = [t for t in target_slots if t not in covered]
    consecutive = 0
    for t in reversed(target_slots):
        if t not in missing:
            break
        consecutive += 1
    outcomes = json.loads(os.environ.get("FX_MONITOR_OUTCOMES", "{}"))
    report = {"checked_at": now.isoformat(), "active_london_day": active(now),
              "status": "CURRENT" if full_ok and (fast_ok or not active(now)) else "DEGRADED",
              "full_source_at": summary.get("collected_at"), "full_age_seconds": full_age,
              "full_fresh": full_ok, "fast_source_at": fast.get("source_at"),
              "fast_age_seconds": fast_age, "fast_observation_valid": fast_ok,
              "fast_last_attempt_at": fast.get("checked_at"), "fast_status": fast.get("status"),
              "target_fast_interval_seconds": 900, "target_full_interval_seconds": 3600,
              "elapsed_fast_targets_today": len(target_slots),
              "unobserved_fast_targets_today": [t.isoformat() for t in missing],
              "consecutive_unobserved_fast_targets": consecutive,
              "continuity_note": "No backfill. Missing observations do not identify GitHub's internal drop/delay cause.",
              "early_cohort_id": early.get("cohort_id"),
              "early_observation_count": len(early.get("source_snapshots", [])),
              "early_last_source_at": (early.get("source_snapshots") or [None])[-1],
              "current_run": {"id": os.environ.get("GITHUB_RUN_ID"),
                              "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                              "event": os.environ.get("GITHUB_EVENT_NAME"),
                              "head_sha": os.environ.get("GITHUB_SHA"),
                              "outcomes": outcomes,
                              "persistence": "PENDING_AT_DIAGNOSTIC_CAPTURE"},
              "previous_publication": read("data/public_dashboard_status.json"),
              "recovery": "ONE_INLINE_FAST_ATTEMPT_PER_HOURLY_RUN_WHEN_DUE",
              "scheduler_guarantee": False}
    if remote:
        try:
            # Query schedule events directly so push/PR traffic cannot hide them.
            runs = github("actions/runs?event=schedule&per_page=100").get("workflow_runs", [])
            report["scheduled"] = {name: schedule_evidence(runs, path) for name,path in (
                ("hourly", ".github/workflows/oanda-hourly-research.yml"),
                ("fast", ".github/workflows/oanda-fast-entry.yml"))}
            workflows = github("actions/workflows").get("workflows", [])
            report["workflow_registration"] = [{k:w.get(k) for k in ("id", "path", "state")}
                for w in workflows if w.get("path") in (".github/workflows/oanda-hourly-research.yml", ".github/workflows/oanda-fast-entry.yml")]
            recent = github("actions/runs?per_page=100").get("workflow_runs", [])
            fast_runs = [r for r in recent if r.get("path") == ".github/workflows/oanda-fast-entry.yml" and r.get("status") == "completed"]
            report["fast_run_failures"] = {"sample": len(fast_runs),
                "failed": sum(r.get("conclusion") != "success" for r in fast_runs),
                "scope": "Latest 100 repository runs; process success is not observation success."}
            # Completed run steps independently expose earlier persistence/publication failures.
            report["last_completed_jobs"] = []
            completed = [r for r in recent if r.get("status") == "completed" and r.get("path") in
                (".github/workflows/oanda-hourly-research.yml", ".github/workflows/oanda-fast-entry.yml")][:2]
            for r in completed:
                jobs = github("actions/runs/"+str(r["id"])+"/jobs").get("jobs", [])
                report["last_completed_jobs"].append({"run_id": r["id"], "event": r["event"],
                    "conclusion": r.get("conclusion"), "failed_steps": [s["name"] for j in jobs
                    for s in j.get("steps", []) if s.get("conclusion") == "failure"]})
        except Exception as exc:
            report["remote_evidence_error"] = type(exc).__name__
            report["scheduler_verification"] = "UNAVAILABLE"
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast-if-due", action="store_true")
    args = parser.parse_args()
    if args.fast_if_due:
        return fast_if_due()
    report = build()
    dest = ROOT / "data/monitoring_health.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    if os.environ.get("GITHUB_RUN_ID"):
        receipt = ROOT / "data/monitoring_runs" / (os.environ["GITHUB_RUN_ID"]+"-"+os.environ.get("GITHUB_RUN_ATTEMPT", "1")+".json")
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(dest.read_text())
    print("Monitoring:", report["status"], "full age", report["full_age_seconds"],
          "fast age", report["fast_age_seconds"], "unobserved targets", report["consecutive_unobserved_fast_targets"])
    if report["status"] != "CURRENT":
        print("::warning::Monitoring degraded; inspect data/monitoring_health.json")
    # Cadence gaps are preserved as evidence; strategy gating remains unchanged.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded 15-minute OANDA entry research. No broker access or automatic orders.

Refreshes only hourly shortlisted and already-frozen pairs. All new plans use
the existing champion admission gates; the challenger remains paper-only.
A detected trigger is *prepared*, never described as delivered to the user.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Existing research modules support both scripts/ execution and unittest imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import oanda_data as market
import news_watch as news_source
import signal_admission as admission
import signal_watch as watcher
import strategy_comparison as comparison
from fx_universe import is_quote_usable, screen_priority

ROOT = Path(__file__).resolve().parents[1]
FAST_LATEST = ROOT / "data/fast_latest.json"
FAST_NEWS = ROOT / "data/fast_news_latest.json"
FAST_NEWS_STATE = ROOT / "data/fast_news_state.json"
FAST_ASSESSMENT = ROOT / "data/fast_entry_assessment.json"
FAST_AUDIT = ROOT / "data/fast_entry_audit.json"
FAST_EVENTS = ROOT / "data/fast_entry_events.json"
FAST_HISTORY = ROOT / "data/fast_entry_history.json"
MAX_PAIRS = 8
MAX_SOURCE_AGE = timedelta(minutes=90)


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def choose_pairs(summary, plans, limit=MAX_PAIRS):
    """First monitor frozen plans, then preliminary qualifiers, then rank.

    The cap and omissions are explicit. No pair becomes an approved trade merely
    because it is shortlisted, and an unsupported GBP/ZAR is always excluded.
    """
    rows = {r.get("instrument"): r for r in summary.get("ranked_review_candidates", [])
            if isinstance(r, dict) and r.get("instrument") != "GBP_ZAR"}
    active = [p.get("instrument") for p in plans.get("signals", [])
              if isinstance(p, dict) and p.get("instrument") != "GBP_ZAR"]
    ranked = sorted(rows, key=lambda p: (-(rows[p].get("research_priority") or 0), p))
    prelim = [p for p in ranked if rows[p].get("deep_entry_scout_eligible")
              or rows[p].get("paper_deep_scout_eligible")]
    ordered = list(dict.fromkeys(active + prelim + ranked))
    chosen = [p for p in ordered if p][:limit]
    return chosen, [p for p in ordered if p and p not in chosen]


def record_events(book, assessment, plans):
    """Immutable discovery clocks, not push receipts or simulated broker fills."""
    events = book.setdefault("events", {})
    by_id = {p["id"]: p for p in plans.get("signals", []) if isinstance(p, dict) and p.get("id")}
    for row in assessment.get("signals", []):
        if row.get("status") != "PRICE_REASSESSMENT_ALERT_READY":
            continue
        key = "|".join(str(row.get(k, "")) for k in ("id", "route", "trigger_bar_open_time"))
        if key in events:
            continue
        source = by_id.get(row.get("id"), {})
        opened = admission.moment(row.get("trigger_bar_open_time"))
        events[key] = {
            "plan_id": row.get("id"), "pair": row.get("pair"),
            "direction": row.get("direction"), "route": row.get("route"),
            "plan_created_at": source.get("created_at"),
            "trigger_bar_open_time": row.get("trigger_bar_open_time"),
            "trigger_closed_at": (opened + timedelta(minutes=(
                5 if source.get("trigger_timeframe") == "M5" else 15))).isoformat()
                if opened else None,
            "trigger_timeframe": source.get("trigger_timeframe", "M15"),
            "first_detected_at": assessment.get("collected_at"),
            "source_snapshot_at": assessment.get("oanda_collected_at"),
            "entry": source.get("planned_entry"), "stop": source.get("stop"),
            "tp1": source.get("tp1"), "delivery_status": "NOT_VERIFIED",
            "verified_user_delivery_at": None, "broker_fill_verified": False,
        }
    book["updated_at"] = assessment.get("collected_at")
    return book


def observation_rows(assessment, plans):
    by_id = {p["id"]: p for p in plans.get("signals", []) if isinstance(p, dict) and p.get("id")}
    out = []
    for row in assessment.get("signals", []):
        plan = by_id.get(row.get("id"), {})
        out.append({
            "id": row.get("id"), "pair": row.get("pair"),
            "direction": row.get("direction"), "status": row.get("status"),
            "reason": row.get("reason"), "route": row.get("route"),
            "trigger_bar_open_time": row.get("trigger_bar_open_time"),
            "entry": plan.get("planned_entry"), "stop": plan.get("stop"),
            "tp1": plan.get("tp1"), "expires_at": plan.get("expires_at"),
            "alert_detected_at": assessment.get("collected_at")
                if row.get("status") == "PRICE_REASSESSMENT_ALERT_READY" else None,
            "user_delivery_verified": False,
            "requires_manual_pepperstone_check": True,
        })
    return out


def perform(now=None):
    now = now or datetime.now(timezone.utc)
    summary = read(ROOT / "data/summary.json", {})
    universe = read(ROOT / "data/universe.json", {})
    previous = read(ROOT / "signals.json", {"signals": []})
    source_at = admission.moment(summary.get("collected_at"))
    if (summary.get("status") not in ("OK", "PARTIAL") or source_at is None
            or not timedelta(0) <= now - source_at <= MAX_SOURCE_AGE
            or universe.get("collected_at") != summary.get("collected_at")):
        result = {"checked_at": now.isoformat(), "status": "STALE_HOURLY_SOURCE",
                  "actionable_count": 0, "observations": [], "user_delivery_verified": False}
        write(FAST_AUDIT, result)
        return result

    pairs, omitted = choose_pairs(summary, previous)
    discovered = universe.get("instruments") or {}
    pairs = [p for p in pairs if p in discovered]
    if not pairs:
        result = {"checked_at": now.isoformat(), "status": "NO_ELIGIBLE_PAIRS",
                  "actionable_count": 0, "omitted": omitted}
        write(FAST_AUDIT, result)
        return result

    account = os.environ.get("OANDA_ACCOUNT_ID", "").strip()
    if not account:
        accounts = market.request("/v3/accounts").get("accounts", [])
        if len(accounts) != 1:
            raise RuntimeError("Set OANDA_ACCOUNT_ID secret for bounded fast scan")
        account = accounts[0]["id"]
    quotes, problems = market.fetch_quote_batch(account, pairs, discovered)
    live = {}
    ranked = []
    for pair in pairs:
        quote = quotes.get(pair)
        if not quote or not is_quote_usable(quote):
            problems[pair] = problems.get(pair, "FAST_INVALID_QUOTE")
            continue
        try:
            frames = {tf: market.fetch_frame(pair, tf, deep=True)
                      for tf in ("M5", "M15", "H1", "H4", "D")}
            frames["M15"]["last_3_completed_candles"] = frames["M15"]["recent_completed_candles"][-3:]
            # A complete entry-check chart includes completed M15 candles.
            rank = screen_priority(quote, frames["M15"], frames["H1"])
            if rank is None:
                problems[pair] = "FAST_SCREEN_UNAVAILABLE"
                continue
            live[pair] = {"quote": quote, "frames": frames}
            ranked.append({"instrument": pair, **rank})
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            problems[pair] = "FAST_" + type(exc).__name__
    if not live:
        result = {"checked_at": datetime.now(timezone.utc).isoformat(),
                  "status": "FAST_MARKET_DATA_UNAVAILABLE",
                  "actionable_count": 0, "problems": problems, "omitted": omitted}
        write(FAST_AUDIT, result)
        return result

    # Separate fast news state keeps the hourly research ledger intact.
    news_source.OUT, news_source.STATE = FAST_NEWS, FAST_NEWS_STATE
    fresh_news = news_source.collect()
    decision = datetime.now(timezone.utc)
    if decision - now > timedelta(minutes=13):
        raise RuntimeError("FAST_ENTRY_SCAN_EXCEEDED_13_MINUTES")
    fast_summary = {"status": "PARTIAL" if problems else "OK",
                    "collected_at": decision.isoformat(),
                    "ranked_review_candidates": ranked, "pairs": live}
    updated, archived, audit = admission.admit(
        fast_summary, fresh_news, previous,
        read(ROOT / "data/signal_plan_archive.json", {"signals": []}),
        evaluated_at=decision.isoformat())
    # Both frozen policies are observed at the same decision time. Costly
    # historical replay stays in the hourly job; no fast run invents a fill.
    study_path = ROOT / "data/strategy_comparison.json"
    study = comparison.update(
        fast_summary, fresh_news, read(study_path, None),
        decided_at=decision.isoformat(), replay_pending=False)
    write(study_path, study)
    # Plans are immutable after creation; the hourly engine subsequently shares
    # these same canonical plans and safety gates.
    if updated != previous:
        write(ROOT / "signals.json", updated)
    if archived.get("signals"):
        write(ROOT / "data/signal_plan_archive.json", archived)
    # Keep the last completed 12 M15 candles; no current unfinished bar.
    for source in live.values():
        for frame in source["frames"].values():
            frame["recent_completed_candles"] = (frame.get("recent_completed_candles") or [])[-12:]
    write(FAST_LATEST, {"status": fast_summary["status"],
                        "collected_at": fast_summary["collected_at"], "pairs": live})

    watcher.SNAPSHOT, watcher.NEWS = FAST_LATEST, FAST_NEWS
    watcher.ASSESSMENT = FAST_ASSESSMENT
    watcher.watch()
    assessment = read(FAST_ASSESSMENT, {})
    events = record_events(read(FAST_EVENTS, {"events": {}}), assessment, updated)
    write(FAST_EVENTS, events)
    observations = observation_rows(assessment, updated)
    record = {"checked_at": assessment.get("collected_at"),
              "source_at": fast_summary["collected_at"],
              "status": "FAST_RESEARCH_CHECK_COMPLETE",
              "candidate_count": len(pairs), "analysed": len(live),
              "omitted_due_to_cap": omitted,
              "problems": problems, "admission": audit,
              "observations": observations,
              "actionable_count": sum(x["status"] == "PRICE_REASSESSMENT_ALERT_READY"
                                      for x in observations),
              "user_delivery_verified": False,
              "research_only": True, "manual_pepperstone_check_required": True}
    write(FAST_AUDIT, record)
    history = read(FAST_HISTORY, {"observations": []})
    history.setdefault("observations", []).append({
        "at": record["checked_at"], "pairs": list(live), "omitted": omitted,
        "candidate_routes": audit.get("candidate_routes", []),
        "alert_events": [x for x in observations
                         if x["status"] == "PRICE_REASSESSMENT_ALERT_READY"]})
    history["observations"] = history["observations"][-300:]
    write(FAST_HISTORY, history)
    print("Fast FX research", record["status"], "analysed", len(live),
          "plans", audit.get("active"), "new triggers", record["actionable_count"])
    return record


if __name__ == "__main__":
    perform()

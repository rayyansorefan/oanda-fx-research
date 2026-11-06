"""Prospectively measure one unsupported-news filter hypothesis, PAPER ONLY.

No policy promotions, broker orders or canonical signals. Immutable hypotheses
are registered *before* a future M15 confirmation and scored from later bid/ask
candles only, using the frozen paper replay and actual observation clocks.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from scripts import paper_trade_audit as paper
except ModuleNotFoundError:
    import paper_trade_audit as paper

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/validation_protocols/FX-GATE-20261106-v1.json"
OUT = ROOT / "data/gate_forward_paper.json"
SOURCE = ROOT / "data/admission_full_gate_audit.json"
SUMMARY = ROOT / "data/summary.json"
OBSERVATIONS = ROOT / "data/fast_entry_history.json"
MAX_NEW_PER_SOURCE = 3
MAX_REPLAYS_PER_RUN = 4
MAX_ACTIVE = 12
MAX_RECORDS = 240
UTC = timezone.utc


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return default


def instant(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t.astimezone(UTC) if t.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def texttime(t):
    return t.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def record_digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def candidate(row, summary, source_at, now, protocol):
    """Eligible counterfactual is genuinely blocked by missing news *only*.

    This never makes a missing primary source equivalent to a cleared event.
    """
    if row.get("route") != "EMA20_PULLBACK" or row.get("failed") != ["news_gate"]:
        return None
    if row.get("news_reason") != "PRIMARY_NEWS_SOURCE_MISSING":
        return None
    checks = row.get("checks") or {}
    if (checks.get("news_gate") is not False
            or not checks
            or any(value is not True for key, value in checks.items() if key != "news_gate")):
        return None
    pair, direction = row.get("pair"), row.get("direction")
    if (not isinstance(pair, str) or len(pair) != 7 or pair[3] != "_"
            or "GBP_ZAR" == pair or direction not in ("LONG", "SHORT")):
        return None
    q = ((summary.get("pairs") or {}).get(pair) or {}).get("quote") or {}
    quote_at = instant(q.get("time"))
    if (q.get("tradeable") is not True or quote_at is None
            or not timedelta(seconds=-60) <= now - quote_at <= timedelta(minutes=5)):
        return None
    bid, ask, spread = number(q.get("bid")), number(q.get("ask")), number(q.get("spread_pips"))
    pip = number(q.get("pip_size"))
    if any(v is None for v in (bid, ask, spread, pip)) or not (0 < bid < ask and spread > 0 and pip > 0):
        return None
    econ = row.get("economic_feasibility_only") or {}
    en, sl, tp = (number(econ.get(k)) for k in ("entry_reference", "stop_reference", "nearest_target"))
    if any(v is None for v in (en, sl, tp)):
        return None
    if not (sl < en < tp if direction == "LONG" else tp < en < sl):
        return None
    if (number(econ.get("gross_rr")) or 0) < 1.5 or (number(econ.get("net_rr")) or 0) < 1.35:
        return None
    name = f"FX-GATE-v1-{source_at.strftime('%Y%m%dT%H%M%S')}-{pair}-{direction}"
    plan = {
        "id": name, "instrument": pair, "direction": direction,
        "created_at": texttime(now),
        "expires_at": texttime(now + timedelta(minutes=int(protocol["expiry_minutes"]))),
        "planned_entry": en, "stop": sl, "tp1": tp,
        "alert_b": en, "max_oanda_spread_pips": round(spread * 1.5, 6),
        "pip_size": pip, "event_blackouts_utc": [],
        "strategy": "PROSPECTIVE_NEWS_COVERAGE_ABLATION_PAPER",
        "source": "OANDA_READ_ONLY", "source_ref": texttime(source_at),
        "original_note": "News source UNVERIFIED; paper-only; neither approval nor a broker entry",
        "technical_context": {"source_bar_time": ((summary.get("pairs") or {}).get(pair) or {}).get("frames", {}).get("M15", {}).get("completed_at")}
    }
    if paper.validate(plan) is not None:
        return None
    return {"id": name, "pair": pair, "route": row["route"],
            "source_at": texttime(source_at), "registered_at": texttime(now),
            "original_gate_row_sha256": record_digest(row),
            "failed_gate": "news_gate", "news_reason": row["news_reason"],
            "policy": protocol["id"], "no_live_entry": True,
            "plan": plan, "result": {"status": "PENDING_TRIGGER", "scored": False},
            "replay_attempts": [], "source_lineage": "CURRENT_FULL_MARKET_SNAPSHOT"}


def observation_times(pair, since, until, summary, history):
    """Admit only actual pair-specific research observations, not intended slots."""
    times = []
    for item in history.get("observations") or []:
        if pair not in (item.get("pairs") or []):
            continue
        when = instant(item.get("at"))
        if when and since < when <= until:
            times.append(when)
    source = instant(summary.get("collected_at"))
    if source and pair in (summary.get("pairs") or {}) and since < source <= until:
        times.append(source)
    return sorted(set(times))


def advance(record, summary, history, now, fetch_bars=None):
    """Never manufacture a fill when the replay data or observations are missing."""
    if (record.get("result") or {}).get("scored") is True:
        return record
    started = instant(record.get("registered_at"))
    if not started or started >= now:
        return record
    plan = record["plan"]
    observations = observation_times(record["pair"], started, now, summary, history)
    try:
        bars = (fetch_bars or paper.source_bars)(plan, now)
        result = paper.replay(plan, bars, now, observation_times=observations)
    except (RuntimeError, ValueError, TypeError, OSError) as error:
        # API failure is a gap, NOT proof of a loss, win, unfilled plan or exit.
        name = str(error)
        error_code = name if name.startswith("OANDA_") else type(error).__name__
        record["replay_attempts"] = (record.get("replay_attempts") or [])[-9:] + [{
            "at": texttime(now), "status": "OBSERVATION_UNAVAILABLE", "error": error_code}]
        return record
    # Never revise a previous scored outcome or an original plan.
    record["result"] = result
    record["replay_attempts"] = (record.get("replay_attempts") or [])[-9:] + [{
        "at": texttime(now), "status": result.get("status"),
        "pair_observation_count": len(observations),
        "candle_evidence_path": getattr(bars, "evidence_path", None)}]
    return record


def update(study, raw, summary, history, protocol, *, now=None, fetch_bars=None):
    now = now or datetime.now(UTC)
    begin = instant(protocol.get("prospective_start_at"))
    if (protocol.get("id") != "FX-GATE-20261106-v1" or protocol.get("no_orders") is not True
            or begin is None or not (0 < int(protocol.get("expiry_minutes", 0)) <= 180)):
        raise ValueError("INVALID_GATE_ABLATION_PROTOCOL")
    book = dict(study or {})
    if book.get("protocol_id") not in (None, protocol["id"]):
        raise ValueError("PROTOCOL_CONFLICT")
    records = dict(book.get("records") or {})
    # The first deployed invocation only activates the protocol. An observation
    # predating that invocation cannot become a newly registered counterfactual.
    activated_at = instant(book.get("activated_at"))
    if activated_at is None:
        activated_at = now
        book["activated_at"] = texttime(now)
    for key in sorted(records):
        if len([r for r in records.values() if (r.get("result") or {}).get("scored") is not True]) == 0:
            break
        row = records[key]
        expiry = instant(row.get("plan", {}).get("expires_at"))
        # Avoid repeated remote requests to already closed hypotheses.
        terminal = {"WIN_TP1", "LOSS_STOP", "TIME_EXIT", "INVALIDATED_PRE_ENTRY",
                    "AMBIGUOUS_ENTRY_BAR", "AMBIGUOUS_EXIT_BAR", "UNTRIGGERED",
                    "TRIGGERED_UNFILLED"}
        if (row.get("result") or {}).get("status") in terminal:
            continue
        if expiry and now > expiry + timedelta(days=6):
            continue
        if sum(1 for x in records.values() if x.get("_scored_this_run")) >= MAX_REPLAYS_PER_RUN:
            break
        records[key] = advance(row, summary, history, now, fetch_bars)
        records[key]["_scored_this_run"] = True
    for record in records.values():
        record.pop("_scored_this_run", None)
    source_at = instant(raw.get("source_at"))
    summary_at = instant(summary.get("collected_at"))
    valid = (now >= begin and source_at is not None and source_at > activated_at and source_at == summary_at
             and summary.get("status") in ("OK", "PARTIAL")
             and timedelta(0) <= now - source_at <= timedelta(minutes=10)
             and raw.get("status") == "DIAGNOSTICS_ONLY")
    added = []
    if valid and len(records) < MAX_RECORDS:
        existing = {(r.get("pair"), instant(r.get("registered_at")).date())
                    for r in records.values() if instant(r.get("registered_at"))}
        active = sum((r.get("result") or {}).get("status") in
                     ("PENDING_TRIGGER", "TRIGGERED_UNFILLED", "OPEN") for r in records.values())
        for row in raw.get("rows") or []:
            rec = candidate(row, summary, source_at, now, protocol)
            if not rec or active >= MAX_ACTIVE or len(added) >= MAX_NEW_PER_SOURCE:
                continue
            ident = (rec["pair"], now.date())
            if rec["id"] in records or ident in existing:
                continue
            records[rec["id"]] = rec
            added.append(rec["id"])
            existing.add(ident)
            active += 1
    outcomes = {}
    for r in records.values():
        status = (r.get("result") or {}).get("status", "UNKNOWN")
        outcomes[status] = outcomes.get(status, 0) + 1
    return {
        "schema": 1, "protocol_id": protocol["id"], "no_orders": True,
        "activated_at": book["activated_at"],
        "last_evaluated_at": texttime(now),
        "status": "PAPER_ONLY_PROSPECTIVE_STUDY" if valid else "NO_ELIGIBLE_NEW_SOURCE",
        "latest_source_at": texttime(source_at) if source_at else None,
        "new_ids": added, "records": records,
        "summary": {"registered": len(records), "new_this_run": len(added),
                    "scored": sum((r.get("result") or {}).get("scored") is True for r in records.values()),
                    "outcomes": outcomes},
        "policy": "Only news-source-missing single-blocker pullback hypotheses; unrelated hard gates remain. No manual alerts, promotions or claims of an executable broker edge."
    }


def main():
    protocol = load(PROTOCOL, {})
    book = update(load(OUT, {}), load(SOURCE, {}), load(SUMMARY, {}),
                  load(OBSERVATIONS, {}), protocol)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    target = OUT.with_suffix(".tmp")
    target.write_text(json.dumps(book, sort_keys=True, indent=2, allow_nan=False) + "\n")
    os.replace(target, OUT)
    print("Prospective gate-paper research", json.dumps(book["summary"], sort_keys=True))


if __name__ == "__main__":
    main()

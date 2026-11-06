#!/usr/bin/env python3
"""Prospective paper-only FX challenger: test news veto bottlenecks, do not waive risk in live signals.

Preserve all original frozen studies and exact entry/exit economics. The only
changed hypothesis is whether unsupported official feed coverage and generic
third-party headline review should veto registration of a PAPER opportunity.

No orders, no canonically approved manual signals, no unverifiable fills.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import FunctionType

try:
    from scripts import simple_paper_shadow_v2 as base
except ModuleNotFoundError:
    import simple_paper_shadow_v2 as base

ROOT = Path(__file__).resolve().parents[1]
VERSION = "contextual-news-shadow-v1"
PROTOCOL = ROOT / "docs/validation_protocols/FX-CONTEXT-20261106-v1.json"
OUT = ROOT / "data/contextual_news_shadow_v1.json"
MAX_REPLAY = 8
RISK_SUSPEND_R = 5.0
MIN_INDEPENDENT_RISK_SAMPLES = 5
UTC = timezone.utc


def read(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def code_digest():
    h = hashlib.sha256()
    for path in (Path(__file__), PROTOCOL, base.ROOT / "scripts/simple_paper_shadow_v2.py"):
        data = path.read_bytes()
        h.update(str(path.name).encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def contextual_news_gate(pair, news, at):
    """Safety-first *paper* decision: missing feed is unknown, not news-clear.

    Do NOT relax a missing/stale economic calendar, known currency-specific high
    impact event, or directly published HIGH primary official announcement.
    Third-party/global narrative and unsupported official-feed coverage remain
    explicitly unverified in the plan evidence (never manual-entry permission).
    """
    observed = base.when(news.get("collected_at"))
    if observed is None or not timedelta(0) <= at - observed <= timedelta(minutes=25):
        return "CONTEXT_NEWS_STALE_OR_UNAVAILABLE"
    information = (news.get("by_pair") or {}).get(pair)
    if not isinstance(information, dict):
        return "CONTEXT_PAIR_NEWS_ABSENT"
    if information.get("calendar_coverage") != "OK" or information.get("near_high_impact_calendar"):
        return "CONTEXT_CALENDAR_UNAVAILABLE_OR_BLACKOUT"
    currencies = set(pair.split("_"))
    for item in information.get("recent_relevant_headlines") or []:
        published = base.when(item.get("published_at"))
        if published is None or not timedelta(0) <= at - published <= timedelta(hours=3):
            continue
        # An actual primary-source HIGH policy announcement is not an unreviewed
        # aggregate narrative. No claim of independent human news clearance.
        if (item.get("severity") == "HIGH"
                and item.get("source") != "Global markets aggregation"
                and item.get("currency_source") in currencies):
            return "CONTEXT_PRIMARY_OFFICIAL_HIGH_IMPACT_REVIEW"
    return None


def news_uncertainty(pair, news, at):
    info = (news.get("by_pair") or {}).get(pair) or {}
    cov = info.get("coverage") or {}
    warnings = []
    if cov.get("missing_required") or cov.get("currencies_without_supported_official_feed"):
        warnings.append("OFFICIAL_COVERAGE_UNVERIFIED")
    if cov.get("status") != "OK":
        warnings.append("NEWS_COVERAGE_PARTIAL_OR_UNVERIFIED")
    if any(base.when(item.get("published_at")) is not None
           and timedelta(0) <= at - base.when(item["published_at"]) <= timedelta(hours=3)
           and item.get("severity", "REVIEW") != "CONTEXT"
           for item in info.get("recent_relevant_headlines") or []):
        warnings.append("MATERIAL_HEADLINE_REVIEW_UNVERIFIED")
    return sorted(set(warnings))


def candidate_proposals(summary, news, at):
    """Clone the original pure proposer with an isolated globals dictionary.

    No monkeypatch, mutation, file edit or strategy lock drift. Technical setup,
    spread, risk, trigger and prices are byte-identical to simple-v2 for the
    same inputs. A new policy/version and separate evidence ledger isolate the
    one altered news-gate hypothesis.
    """
    env = dict(base.propose.__globals__)
    env["news_gate"] = contextual_news_gate
    env["VERSION"] = VERSION
    propose = FunctionType(base.propose.__code__, env, name="contextual_propose",
                           argdefs=base.propose.__defaults__, closure=base.propose.__closure__)
    result = propose(summary, news, at)
    for plan in result:
        pair = plan["instrument"]
        plan["policy"] = VERSION
        plan["comparison_news_gate"] = base.news_gate(pair, news, at) or "ORIGINAL_GATE_PASSED"
        plan["news_policy"] = "CONTEXTUAL_PAPER_RISK_TAGGED"
        plan["unverified_news"] = news_uncertainty(pair, news, at)
        plan["economic_levels_unchanged_from_simple_v2"] = True
        plan["approved_entry"] = False
        plan["broker_order_permitted"] = False
    return result


def independent_scores(records):
    """One earliest actual scored fill per pair per London day, deterministic."""
    from zoneinfo import ZoneInfo
    london = ZoneInfo("Europe/London")
    rows = []
    for item in records.values():
        result = item.get("result") or {}
        at = base.when(result.get("entry_at"))
        if result.get("scored") is not True or at is None:
            continue
        stressed = base.num(result.get("net_r_stressed_illustrative"))
        if stressed is None:
            continue
        pair = item["plan"]["instrument"]
        rows.append((at, pair, stressed))
    rows.sort(key=lambda r: (r[0], r[1]))
    seen = set()
    independent = []
    for entered, pair, r in rows:
        key = (pair, entered.astimezone(london).date().isoformat())
        if key not in seen:
            seen.add(key)
            independent.append((entered, pair, r))
    return independent


def risk_state(book):
    """Stickily suspend new paper plans after pre-registered 5R drawdown.

    A risk suspension is NOT statistical proof of an unprofitable strategy;
    it never rewrites prior outcomes and never closes simulated filled trades.
    """
    rows = independent_scores(book["records"])
    total = 0.0
    peak = 0.0
    drawdown = 0.0
    for _, _, net_r in rows:
        total += net_r
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
    previous = (book.get("risk") or {}).get("status")
    suspended = previous == "SUSPENDED_NEW_PAPER_ENTRIES" or (
        len(rows) >= MIN_INDEPENDENT_RISK_SAMPLES and drawdown >= RISK_SUSPEND_R)
    return {
        "status": "SUSPENDED_NEW_PAPER_ENTRIES" if suspended else "FORWARD_PAPER_ONLY",
        "independent_scored_pair_days": len(rows),
        "total_cost_stressed_r": round(total, 6) if rows else None,
        "max_drawdown_stressed_r": round(drawdown, 6) if rows else None,
        "risk_suspension_threshold_r": RISK_SUSPEND_R,
        "minimum_samples_to_suspend": MIN_INDEPENDENT_RISK_SAMPLES,
        "automatic_promotion": False,
        "interpretation": "Risk circuit-breaker only. No live trading or profitability claim."
    }


def update(summary, news, book=None, fetch=None, *, now=None):
    now = base.when(now) if isinstance(now, str) else (now or datetime.now(UTC))
    if now.tzinfo is None:
        raise ValueError("UTC_REQUIRED")
    rule = read(PROTOCOL, {})
    if rule.get("id") != "FX-CONTEXT-20261106-v1" or rule.get("promotion_allowed") is not False:
        raise ValueError("CONTEXT_PROTOCOL_INVALID")
    # Must independently verify that the original frozen simple-v2 source has
    # not changed. Never use the new study to reset or rewrite its ledger.
    base.validate_protocol(read(base.PROTOCOL, {}))
    digest = code_digest()
    if book is None:
        book = {"schema": 1, "study_version": VERSION, "registered_at": base.iso(now),
                "holdout_start_at": base.iso(now), "rule_sha256": digest,
                "records": {}, "pair_observations": {}, "source_snapshots": [],
                "research_only": True, "broker_orders_submitted": 0}
    if book.get("study_version") != VERSION or book.get("rule_sha256") != digest:
        raise ValueError("CONTEXT_FROZEN_RULES_CHANGED")
    registered = base.when(book.get("registered_at"))
    if registered is None or now < registered:
        raise ValueError("CONTEXT_AS_OF_BEFORE_REGISTRATION")
    previous_risk = risk_state(book)
    observed_at = summary.get("collected_at")
    source = base.when(observed_at)
    new_source = (
        source is not None and source <= now
        and timedelta(0) <= now - source <= timedelta(minutes=15)
        and summary.get("status") in ("OK", "PARTIAL")
        and observed_at not in book["source_snapshots"]
    )
    if new_source:
        book["source_snapshots"].append(observed_at)
        for pair in base.PAIRS:
            q = ((summary.get("pairs") or {}).get(pair) or {}).get("quote") or {}
            qt = base.when(q.get("time"))
            bid, ask = base.num(q.get("bid")), base.num(q.get("ask"))
            if (qt is not None and q.get("tradeable") is True
                    and timedelta(seconds=-60) <= now - qt <= timedelta(minutes=5)
                    and bid is not None and ask is not None and 0 < bid < ask):
                book["pair_observations"].setdefault(pair, []).append(base.iso(now))
        if previous_risk["status"] != "SUSPENDED_NEW_PAPER_ENTRIES":
            for plan in candidate_proposals(summary, news, now):
                overlap = any(
                    old["plan"]["instrument"] == plan["instrument"]
                    and old["plan"]["setup_type"] == plan["setup_type"]
                    and base.when(old["plan"]["expires_at"]) > now
                    for old in book["records"].values()
                )
                if not overlap and plan["id"] not in book["records"]:
                    book["records"][plan["id"]] = {
                        "plan": plan, "result": {"status": "PENDING_TRIGGER", "scored": False}}
    pending = [r for r in book["records"].values()
               if (r.get("result") or {}).get("status") not in base.TERMINAL]
    for entry in sorted(pending, key=lambda x: x["plan"]["created_at"])[:MAX_REPLAY]:
        plan = entry["plan"]
        try:
            candles = fetch(plan, now) if fetch else base.source_bars(plan, now)
            observations = [base.when(t) for t in book["pair_observations"].get(plan["instrument"], [])]
            candidate = base.replay(plan, candles, now, [t for t in observations if t is not None])
            if getattr(candles, "evidence_path", None):
                candidate["source_evidence"] = candles.evidence_path
            entry["result"] = base.reconcile(entry, candidate, base.iso(now))
        except (OSError, RuntimeError, TypeError, ValueError, KeyError, TimeoutError) as exc:
            entry["result"] = base.reconcile(entry, {
                "status": "SOURCE_UNAVAILABLE", "scored": False,
                "reason": type(exc).__name__}, base.iso(now))
    for route in base.ROUTES:
        base.apply_portfolio_limits({
            key: item for key, item in book["records"].items()
            if item["plan"]["setup_type"] == route})
    score = risk_state(book)
    outcomes = Counter(x["result"].get("status") for x in book["records"].values())
    scored = [x["result"] for x in book["records"].values() if x["result"].get("scored") is True]
    book["risk"] = score
    book["status"] = score["status"]
    book["checked_at"] = base.iso(now)
    book["metrics"] = {
        "plans": len(book["records"]), "scored_exits": len(scored),
        "filled": sum(bool(x["result"].get("entry_at")) for x in book["records"].values()),
        "outcomes": dict(outcomes),
        "independent_scored_pair_days": score["independent_scored_pair_days"],
        "net_total_r_bidask": round(sum(x["net_r_bidask"] for x in scored), 6) if scored else None,
        "net_total_r_cost_stress": round(sum(x["net_r_stressed_illustrative"] for x in scored), 6) if scored else None,
        "additional_paper_approvals": 0, "verified_user_deliveries": 0,
        "broker_costs_verified": False, "performance_validated": False,
        "paper_only": True,
        "news_uncertainty_plans": sum(bool(x["plan"]["unverified_news"]) for x in book["records"].values()),
        "relaxed_news_gate_plans": sum(x["plan"]["comparison_news_gate"] != "ORIGINAL_GATE_PASSED"
                                        for x in book["records"].values()),
    }
    book["broker_orders_submitted"] = 0
    book["automatic_promotion_allowed"] = False
    return book


def main():
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "--fast"):
        raise SystemExit("Only --fast optional mode supported")
    fast = len(sys.argv) == 2
    now = datetime.now(UTC)
    summary_path = ROOT / ("data/fast_latest.json" if fast else "data/summary.json")
    news_path = ROOT / ("data/fast_news_latest.json" if fast else "data/news_latest.json")
    result = update(read(summary_path, {}), read(news_path, {}),
                    read(OUT, None), now=now)
    write(OUT, result)
    print("Contextual news prospective challenger", "FAST" if fast else "FULL",
          result["status"], result["metrics"])


if __name__ == "__main__":
    main()

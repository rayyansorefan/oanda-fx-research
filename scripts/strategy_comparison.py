"""Pre-registered champion/challenger paper study. Never writes signals.json.

V1 is a hypothesis, not a validated improvement. Same costs, target geometry,
manual response delay and actual collector observation times for both arms.
Historical development samples cannot qualify a challenger for promotion.
"""
from __future__ import annotations
import json
import math
from collections import Counter
from pathlib import Path
from datetime import timedelta, datetime, timezone
try:
    from scripts.signal_admission import qualify, moment, number
    from scripts.paper_trade_audit import source_bars, replay, iso
except ModuleNotFoundError:
    from signal_admission import qualify, moment, number
    from paper_trade_audit import source_bars, replay, iso

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ("champion", "challenger_v1")
TERMINAL = {"WIN_TP1", "LOSS_STOP", "TIME_EXIT", "UNTRIGGERED",
            "INVALIDATED_PRE_ENTRY", "AMBIGUOUS_ENTRY_BAR", "AMBIGUOUS_EXIT_BAR"}

def freeze(summary, news, source_ref=None, *, decided_at=None):
    source_time = moment(summary.get("collected_at"))
    now = moment(decided_at) if decided_at is not None else source_time
    rows = []
    if not now or not source_time or now < source_time or summary.get("status") not in ("OK", "PARTIAL"):
        return rows
    for policy in POLICIES:
        admitted = set()
        for ranked in sorted(summary.get("ranked_review_candidates") or [],
                             key=lambda r: -(number(r.get("research_priority")) or 0)):
            pair = ranked.get("instrument")
            if not pair or pair in admitted:
                continue
            for route in ("MOMENTUM_CONTINUATION", "EMA20_PULLBACK"):
                plan, reason = qualify(pair, ranked, (summary.get("pairs") or {}).get(pair) or {},
                                       news, now, route, policy=policy)
                rows.append({"policy": policy, "source_at": summary["collected_at"],
                    "source_ref": source_ref, "study_method_version":2, "decision_at":iso(now),
                    "common_deep_history_available":all(
                        ((summary.get("pairs") or {}).get(pair,{}).get("frames",{}).get(tf,{}) or {}).get("indicators")
                        for tf in ("M15","H1","H4","D")), "pair": pair, "route": route,
                    "reason": reason, "plan": plan})
                if plan:
                    plan["oanda_snapshot_at"] = summary["collected_at"]
                    admitted.add(pair)
                    break
            if len(admitted) >= 3:
                break
    return rows

def metrics(records):
    out = {}
    for policy in POLICIES:
        selected = [r for r in records if r["policy"] == policy]
        plans = [r for r in selected if r.get("plan")]
        results = [r.get("result") or {} for r in plans]
        counts = Counter(r.get("status", "NOT_REPLAYED") for r in results)
        scored = [r for r in results if r.get("scored") is True]
        values = [float(r["net_r"]) for r in scored]
        equity = peak = drawdown = 0.0
        for r in sorted(scored, key=lambda x: x.get("at") or ""):
            equity += float(r["net_r"])
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
        mean = sum(values)/len(values) if values else None
        sd = (sum((v-mean)**2 for v in values)/(len(values)-1))**.5 if len(values)>1 else None
        out[policy] = {"signals_generated":len(plans),
            "signals_triggered":sum(bool(r.get("triggered_at")) for r in results),
            "filled":sum(bool(r.get("entry_at")) for r in results),
            "tp1":counts["WIN_TP1"], "stops":counts["LOSS_STOP"],
            "outcomes":dict(counts), "rejections":dict(Counter(r["reason"] for r in selected if r.get("reason"))),
            "net_total_r":round(sum(values),5) if values else None,
            "max_drawdown_r":round(drawdown,5) if values else None,
            "mean_net_r":mean,
            "unannualised_mean_over_sd":mean/sd if len(values)>=30 and sd else None,
            "false_breakout_stop_proxy":sum(r.get("route")=="C" and r.get("status")=="LOSS_STOP" for r in results),
            "incorrect_rejections_verified":0,
            "missed_profitable_entries_verified":0}
    return out

def update(summary, news, book=None, fetch=None, *, decided_at=None):
    book = book or {"schema":1, "records":[], "observation_times":[]}
    source_time = moment(summary.get("collected_at"))
    now = moment(decided_at) if decided_at is not None else source_time
    if not now or not source_time or now < source_time:
        raise ValueError("Missing or backdated study timestamp")
    observation = iso(now)
    seen_sources = set(book.get("source_snapshots") or []) | {r["source_at"] for r in book["records"]}
    if summary["collected_at"] not in seen_sources:
        book.setdefault("source_snapshots", []).append(summary["collected_at"])
        book["observation_times"].append(observation)
        existing = {(r["policy"],r["pair"],r["route"],
                     (r.get("plan",{}).get("technical_context") or {}).get("source_bar_time"))
                    for r in book["records"] if r.get("plan")}
        for row in freeze(summary, news, decided_at=iso(now)):
            plan = row.get("plan")
            key = (row["policy"],row["pair"],row["route"],
                   ((plan or {}).get("technical_context") or {}).get("source_bar_time"))
            if plan and key in existing:
                continue
            if plan:
                existing.add(key)
            book["records"].append(row)
    from zoneinfo import ZoneInfo
    observations = [moment(t) for t in book["observation_times"]
                    if 7 <= moment(t).astimezone(ZoneInfo("Europe/London")).hour < 23]
    # Resolve older paired cohorts first; never fabricate a no-fill on source failure.
    pending = [r for r in book["records"] if r.get("plan") and
               (r.get("result") or {}).get("status") not in TERMINAL]
    for row in pending[:12]:
        try:
            bars = (fetch(row["plan"],now) if fetch else source_bars(row["plan"],now))
            row["result"] = replay(row["plan"], bars, now, observation_times=observations)
            if getattr(bars,"evidence_path",None):
                row["result"]["source_evidence"] = bars.evidence_path
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            row["result"] = {"status":"SOURCE_UNAVAILABLE", "scored":False,
                             "reason":type(exc).__name__}
    book.update(updated_at=observation, metrics=metrics(book["records"]),
        performance_validated=False, promotion_allowed=False,
        policy={"version":"20261028-v2", "challenger":"M15/H1 EMA+DMI; H1 ADX>=18; H4/D context not compulsory. Same exhaustion, stop, nearest-target, gross>=1.50/net>=1.35, news and spread gates.",
            "timing":"Actual study decision times plus 1 minute manual delay, rounded to next M15 opening; no assumed pattern-close fill.",
            "costs":"OANDA ask buys/bid sells and opposite exit side; no second spread deduction in realised returns. Slippage, financing and Pepperstone fills unknown.",
            "drawdown":"Sum of per-trade net R, not portfolio/account drawdown; overlapping correlated trades not a portfolio model.",
            "validation":"Prospective frozen rules; historical development cohorts separate; no automatic promotion. Insufficient data is not zero expected risk."})
    return book

def main():
    path = ROOT/"data/strategy_comparison.json"
    book = json.loads(path.read_text()) if path.exists() else None
    result = update(json.loads((ROOT/"data/summary.json").read_text()),
                    json.loads((ROOT/"data/news_latest.json").read_text()), book,
                    decided_at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("Paper-only strategy comparison", json.dumps(result["metrics"],sort_keys=True))

if __name__=="__main__":
    main()

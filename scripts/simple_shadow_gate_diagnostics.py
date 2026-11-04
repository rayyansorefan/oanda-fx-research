"""Transparent first-blocker telemetry for frozen paper shadow gates.

This is source-only diagnostics, NOT a strategy, trade, or approval. It never
alters immutable shadow rules, trading ledgers or protocol registration.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from scripts import simple_paper_shadow_v2 as sh
except ModuleNotFoundError:
    import simple_paper_shadow_v2 as sh

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/simple_shadow_gate_diagnostics.json"
EARLY_PROTOCOL = ROOT / "docs/validation_protocols/FX-EARLY-20261104-v1.json"


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else (default if default is not None else {})


def first_blocker(pair, summary, news, now):
    """Return the first exact v2 decision gate that rejects this pair.

    Multiple independent blockers may exist; never treat this as filter
    ablation evidence or a hypothetical profitable trade.
    """
    src = (summary.get("pairs") or {}).get(pair) or {}
    q = src.get("quote") or {}
    t = sh.when(q.get("time"))
    if not src:
        return "PAIR_SOURCE_MISSING"
    if (q.get("tradeable") is not True or not t
            or not timedelta(seconds=-60) <= now-t <= timedelta(minutes=5)):
        return "QUOTE_MISSING_STALE_OR_NOT_TRADEABLE"
    bid, ask = sh.num(q.get("bid")), sh.num(q.get("ask"))
    pip = sh.num(q.get("pip_size")) or sh.pip_size(pair)
    if bid is None or ask is None or not (0 < bid < ask):
        return "BID_ASK_INVALID"
    spread = ask-bid
    if spread/pip > (3 if pair.endswith("_JPY") else 2.5):
        return "SPREAD_TOO_WIDE"
    frames = src.get("frames") or {}
    for tf, minutes in (("M15", 15), ("H1", 60)):
        start = sh.when((frames.get(tf) or {}).get("completed_at"))
        if (not start or start + timedelta(minutes=minutes) > now
                or now - (start + timedelta(minutes=minutes)) > timedelta(minutes=minutes)):
            return "COMPLETED_" + tf + "_FRAME_MISSING_OR_STALE"
    m = (frames.get("M15") or {}).get("indicators") or {}
    h = (frames.get("H1") or {}).get("indicators") or {}
    ma, mb, ha, hb, atr = [sh.num(v) for v in
        (m.get("ema20"), m.get("ema50"), h.get("ema20"),
         h.get("ema50"), m.get("atr14"))]
    if any(v is None for v in (ma, mb, ha, hb, atr)) or atr <= 0:
        return "EMA_OR_ATR_MISSING"
    direction = ("LONG" if ma > mb and ha > hb else
                 "SHORT" if ma < mb and ha < hb else None)
    if direction is None:
        return "M15_H1_EMA_DIRECTION_CONFLICT"
    if spread > .20*atr:
        return "SPREAD_RELATIVE_TO_ATR"
    bars = (frames.get("M15") or {}).get("last_3_completed_candles") or []
    if len(bars) < 3:
        return "THREE_COMPLETED_M15_BARS_MISSING"
    last_t = sh.when(bars[-1].get("time"))
    if (not last_t or last_t+timedelta(minutes=15) > now
            or now-(last_t+timedelta(minutes=15)) > timedelta(minutes=25)):
        return "LATEST_COMPLETED_M15_BAR_STALE"
    try:
        last = bars[-1]["mid"]
        op, hi, lo, cl = (float(last[k]) for k in ("o", "h", "l", "c"))
    except (TypeError, KeyError, ValueError):
        return "LATEST_M15_BAR_INVALID"
    reason = sh.news_gate(pair, news, now)
    if reason:
        return "NEWS_" + reason
    sign = 1 if direction == "LONG" else -1
    entry = ask if sign > 0 else bid
    risk = 1.25*atr
    decimals = 3 if pip >= .01 else 5
    stop = round(entry-sign*risk, decimals)
    target = round(entry+sign*1.8*risk, decimals)
    if not (stop < entry < target if sign > 0 else target < entry < stop):
        return "STOP_TARGET_GEOMETRY_INVALID"
    net_rr = (abs(target-entry)-spread)/(abs(entry-stop)+spread)
    if net_rr < 1.35:
        return "NET_RR_BELOW_FROZEN_FLOOR"
    impulse = (sh.close_to_end(bars[-1], direction)
               and sign*(cl-op) >= .35*atr
               and abs(cl-ma) <= 1.25*atr)
    pullback = (lo <= ma <= hi and sh.close_to_end(bars[-1], direction)
                and abs(cl-ma) <= .60*atr)
    if not impulse and not pullback:
        return "NO_COMPLETED_MOMENTUM_OR_PULLBACK_SETUP"
    return "FORMED_PAPER_SETUP_REQUIRES_PROSPECTIVE_REGISTRATION"


def collect(summary=None, news=None, now=None, state=None):
    now = sh.when(now) if isinstance(now, str) else (now or datetime.now(timezone.utc))
    early = load(EARLY_PROTOCOL)
    start = sh.when(early.get("holdout_start_at"))
    if start is None or early.get("id") != "FX-EARLY-20261104-v1":
        raise ValueError("EARLY_PROSPECTIVE_PROTOCOL_MISSING")
    summary = summary if summary is not None else load(ROOT/"data/summary.json")
    news = news if news is not None else load(ROOT/"data/news_latest.json")
    state = state if state is not None else load(OUT, {
        "observations": [], "total_pair_gate_counts": {}, "total_evaluated_snapshots": 0,
    })
    source = sh.when(summary.get("collected_at"))
    # Diagnostics cannot authenticate a missed, old or failed observation.
    if (now < start or summary.get("status") not in ("OK", "PARTIAL")
            or source is None or not timedelta(0) <= now-source <= timedelta(minutes=15)
            or any(row.get("source_at") == summary.get("collected_at")
                   for row in state.get("observations", []))):
        return state
    pair_reasons = {p:first_blocker(p,summary,news,now) for p in sh.PAIRS}
    counts = Counter(pair_reasons.values())
    state.setdefault("observations", []).append({
        "source_at": summary["collected_at"],
        "evaluated_at": sh.iso(now),
        "pair_reasons": pair_reasons,
        "first_blocker_counts": dict(counts),
        "paper_plans_not_inferred": True,
    })
    state["observations"] = state["observations"][-200:]
    state["total_evaluated_snapshots"] = int(state.get("total_evaluated_snapshots", 0))+1
    totals = Counter(state.get("total_pair_gate_counts") or {})
    totals.update(counts)
    state["total_pair_gate_counts"] = dict(totals)
    state["last_source_at"] = summary["collected_at"]
    state["scope"] = "FIRST_BLOCKER_DIAGNOSTIC_NOT_AN_ELIGIBLE_FILL"
    state["registered_cohort"] = early["id"]
    state["performance_validated"] = False
    state["no_broker_orders"] = True
    return state


def main():
    result = collect()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("Frozen paper shadow first-blocker diagnostics",
          result.get("last_source_at"), result.get("total_pair_gate_counts", {}))


if __name__ == "__main__":
    main()

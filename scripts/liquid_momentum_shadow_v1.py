"""Frozen prospective liquid-pair momentum challenger (paper only).

Uses the existing bid/ask forward scoring machinery without mutating the
champion, v2 or contextual study. Never approves or sends a live trade.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import FunctionType

try:
    from scripts import simple_paper_shadow_v2 as base
    from scripts import contextual_news_shadow as contextual
except ModuleNotFoundError:
    import simple_paper_shadow_v2 as base
    import contextual_news_shadow as contextual

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/validation_protocols/FX-LIQUID-MOMENTUM-20261109-v1.json"
OUT = ROOT / "data/liquid_momentum_shadow_v1.json"
VERSION = "liquid-momentum-shadow-v1"
START = "2026-11-09T07:00:00Z"
PAIRS = ("EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF",
         "USD_CAD", "AUD_USD", "NZD_USD", "EUR_JPY")
ROUTES = ("MOMENTUM_CONTINUATION",)
UTC = timezone.utc


def digest():
    # Lock every dependency that can change market, news or fill economics.
    h = hashlib.sha256()
    paths = (Path(__file__), PROTOCOL,
             ROOT/"scripts/simple_paper_shadow_v2.py",
             ROOT/"scripts/contextual_news_shadow.py",
             ROOT/"scripts/paper_evidence_guard.py",
             ROOT/"scripts/paper_trade_audit.py",
             ROOT/"scripts/oanda_data.py")
    for path in paths:
        h.update(str(path.relative_to(ROOT)).encode() + b"\\x00")
        h.update(path.read_bytes())
    return h.hexdigest()


def validate_protocol():
    p = json.loads(PROTOCOL.read_text())
    if (p.get("id") != "FX-LIQUID-MOMENTUM-20261109-v1"
            or p.get("study_version") != VERSION
            or p.get("holdout_start_at") != START
            or tuple(p.get("pairs") or ()) != PAIRS
            or p.get("validation", {}).get("automatic_live_promotion") is not False
            or p.get("rules", {}).get("no_orders") is not True):
        raise ValueError("LIQUID_MOMENTUM_PROTOCOL_INVALID")
    return p


def finite(value):
    x = base.num(value)
    return x if x is not None and math.isfinite(x) else None


def _trend(indicators):
    x, y = finite(indicators.get("ema20")), finite(indicators.get("ema50"))
    if x is None or y is None or x == y:
        return None
    return "LONG" if x > y else "SHORT"


def propose(summary, news, now):
    """Freeze future M15 continuation plans without using a future price.

    This is a prospective paper experiment, NOT a manual Pepperstone entry.
    The later independent M15 close and executable-side quote must both occur
    after this registration; base.replay models late observation and no-fills.
    """
    at = base.when(now) if isinstance(now, str) else now
    start = base.when(START)
    collected = base.when(summary.get("collected_at"))
    if (at is None or at.tzinfo is None or at < start
            or summary.get("status") not in ("OK", "PARTIAL")
            or collected is None or not timedelta(0) <= at-collected <= timedelta(minutes=15)):
        return []
    plans = []
    for pair in PAIRS:
        source = (summary.get("pairs") or {}).get(pair) or {}
        quote = source.get("quote") or {}
        qt = base.when(quote.get("time"))
        if (qt is None or quote.get("tradeable") is not True
                or not timedelta(seconds=-60) <= at-qt <= timedelta(minutes=5)):
            continue
        bid, ask, pip = [finite(quote.get(k)) for k in ("bid", "ask", "pip_size")]
        if None in (bid, ask, pip) or not (0 < bid < ask and pip > 0):
            continue
        spread, spread_pips = ask-bid, (ask-bid)/pip
        if not (0 < spread_pips <= 2.5):
            continue
        frames = source.get("frames") or {}
        m15, h1 = (frames.get("M15") or {}), (frames.get("H1") or {})
        mt, ht = base.when(m15.get("completed_at")), base.when(h1.get("completed_at"))
        if (mt is None or ht is None
                or mt+timedelta(minutes=15) > at
                or at-(mt+timedelta(minutes=15)) > timedelta(minutes=18)
                or ht+timedelta(hours=1) > at
                or at-(ht+timedelta(hours=1)) > timedelta(minutes=75)):
            continue
        mi, hi = m15.get("indicators") or {}, h1.get("indicators") or {}
        direction = _trend(mi)
        if direction is None or _trend(hi) != direction:
            continue
        atr, adx, ema = finite(mi.get("atr14")), finite(hi.get("adx14")), finite(mi.get("ema20"))
        if (atr is None or atr <= 0 or adx is None or adx < 18
                or ema is None or spread > 0.20*atr):
            continue
        candles = (m15.get("last_3_completed_candles") or [])
        if len(candles) < 3:
            continue
        bars = candles[-3:]
        bar_time = base.when(bars[-1].get("time"))
        if bar_time is None or bar_time != mt:
            continue
        try:
            mid = [b["mid"] for b in bars]
            open_, high, low, close = [float(mid[-1][key]) for key in ("o", "h", "l", "c")]
            previous_high = max(float(x["h"]) for x in mid[-3:-1])
            previous_low = min(float(x["l"]) for x in mid[-3:-1])
            swing_high = max(float(x["h"]) for x in mid)
            swing_low = min(float(x["l"]) for x in mid)
        except (ValueError, TypeError, KeyError):
            continue
        span = high-low
        if span <= 0 or abs(close-open_)/span < 0.35:
            continue
        sign = 1 if direction == "LONG" else -1
        close_quality = (close-low)/span if sign > 0 else (high-close)/span
        if close_quality < 0.65 or sign*(close-open_) <= 0:
            continue
        if not (close > previous_high if sign > 0 else close < previous_low):
            continue
        entry = ask if sign > 0 else bid
        if (sign*(entry-close) > 0.30*atr
                or abs(entry-ema) > 1.5*atr):
            continue
        if contextual.contextual_news_gate(pair, news, at):
            continue
        risk = max(1.1*atr,
                   (entry-swing_low+0.1*atr) if sign > 0
                   else (swing_high-entry+0.1*atr))
        if not (risk > 0 and risk <= 2.5*atr):
            continue
        stop, target = entry-sign*risk, entry+sign*1.8*risk
        conservative_cost = spread + 0.5*pip  # illustrative, not Pepperstone verified
        net_rr = (abs(target-entry)-conservative_cost)/(risk+conservative_cost)
        if net_rr < 1.35:
            continue
        trigger = swing_high+pip if sign > 0 else swing_low-pip
        decimals = 3 if pip >= 0.01 else 5
        entry_r, stop_r, tp_r, trigger_r = [round(x, decimals) for x in
                                           (entry, stop, target, trigger)]
        if not (stop_r < entry_r < tp_r if sign > 0 else tp_r < entry_r < stop_r):
            continue
        if (abs(tp_r-entry_r)-conservative_cost)/(abs(entry_r-stop_r)+conservative_cost) < 1.35:
            continue
        name = (VERSION + "-" + at.strftime("%Y%m%dT%H%M%SZ")
                + "-" + pair + "-MOMENTUM_CONTINUATION")
        plans.append({
            "id": name, "policy": VERSION, "instrument": pair,
            "direction": direction, "setup_type": "MOMENTUM_CONTINUATION",
            "created_at": base.iso(at), "expires_at": base.iso(at+timedelta(minutes=90)),
            "source_at": summary["collected_at"], "last_source_bar_at": base.iso(bar_time),
            "trigger": trigger_r, "indicative_entry": entry_r,
            "stop": stop_r, "tp1": tp_r, "pip_size": pip,
            "atr_at_decision": atr,
            "max_spread_pips": round(min(2.5, max(1.0, spread_pips*1.5)), 4),
            "max_chase_atr": 0.35, "manual_response_seconds": 60,
            "gross_rr": round(abs(tp_r-entry_r)/abs(entry_r-stop_r), 4),
            "estimated_net_rr_before_slippage": round(net_rr, 4),
            "experimental_paper_only": True, "broker_order_permitted": False,
            "approved_entry": False, "user_delivery_verified": False,
            "news_uncertainty": contextual.news_uncertainty(pair, news, at),
            "entry_condition": "FUTURE_COMPLETED_M15_CLOSE_BEYOND_FROZEN_TRIGGER",
            "invalidation": "PREENTRY_STOP_TOUCH_OR_EXPIRY",
            "execution_cost_assumption": "OANDA bid-ask plus illustrative 0.5 pip stress",
        })
    return plans


def update(summary, news, book=None, fetch=None, *, now=None):
    """Run unchanged OANDA forward replay in a fresh isolated study identity."""
    validate_protocol()
    code_hash = digest()
    if book is not None and book.get("rule_sha256") != code_hash:
        raise ValueError("LIQUID_MOMENTUM_FROZEN_RULES_CHANGED")
    env = dict(base.update.__globals__)
    env.update({"VERSION": VERSION, "START": START, "PAIRS": PAIRS,
                "ROUTES": ROUTES, "propose": propose,
                "MAX_REPLAYS_PER_SNAPSHOT": 4})
    researcher = FunctionType(base.update.__code__, env,
                              name="liquid_momentum_update",
                              argdefs=base.update.__defaults__,
                              closure=base.update.__closure__)
    result = researcher(summary, news, book, fetch, now=now)
    result["rule_sha256"] = code_hash
    result["research_only"] = True
    result["automatic_promotion_allowed"] = False
    result["performance_validated"] = False
    result["strategy_lifecycle_note"] = ("No live admissions or broker orders; "
        "separate 8-liquid-pair momentum holdout started 9 November 2026. "
        "Any later live change requires independent profitability evidence.")
    return result


def main():
    validate_protocol()
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "--fast"):
        raise SystemExit("Usage: liquid_momentum_shadow_v1.py [--fast]")
    fast = len(sys.argv) == 2
    summary_path = ROOT / ("data/fast_latest.json" if fast else "data/summary.json")
    news_path = ROOT / ("data/fast_news_latest.json" if fast else "data/news_latest.json")
    summary = json.loads(summary_path.read_text())
    news = json.loads(news_path.read_text())
    book = json.loads(OUT.read_text()) if OUT.exists() else None
    outcome = update(summary, news, book)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(outcome, sort_keys=True, indent=2, allow_nan=False) + "\n")
    print("Liquid momentum forward paper cohort:", outcome["metrics"],
          "| source:", summary.get("collected_at"),
          "| mode:", "fast" if fast else "full")


if __name__ == "__main__":
    main()

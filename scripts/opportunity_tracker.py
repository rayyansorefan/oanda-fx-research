#!/usr/bin/env python3
"""Forward-only monitoring of early FX continuation and pullback research.

This is an opportunity audit, NOT a trade or an order simulator. A favourable
price move without an independent entry fill is never a scored win. New setups
are first registered at the actual OANDA snapshot that observed them, and
older candles cannot be retrospectively registered as an early opportunity.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

try:
    from scripts.export_public_dashboard import build_research_setups
except ModuleNotFoundError:  # direct execution: python scripts/opportunity_tracker.py
    from export_public_dashboard import build_research_setups

SUMMARY = Path("data/summary.json")
LEDGER = Path("data/entry_opportunity_ledger.json")
ROUTES = ("MOMENTUM_CONTINUATION", "EMA20_PULLBACK")


def moment(value):
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except ValueError:
        return None


def number(x):
    try:
        value = float(x)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _quote(source, snapshot):
    quote = (source or {}).get("quote") or {}
    bid, ask = number(quote.get("bid")), number(quote.get("ask"))
    at = moment(quote.get("time"))
    if None in (bid, ask) or not (0 < bid < ask) or not at:
        return None
    if at > snapshot + timedelta(minutes=1) or snapshot - at > timedelta(minutes=10):
        return None
    return {"bid": bid, "ask": ask, "at": at}


def _move_pct(row, quote):
    # OANDA exit side versus the original observed indicative reference price;
    # NOT a filled position, broker profit or margin/account performance.
    first = number(row.get("reference_mid"))
    if not first or first <= 0:
        return None
    close_side = quote["bid"] if row["direction"] == "LONG" else quote["ask"]
    return round(100 * (close_side - first) / first *
                 (1 if row["direction"] == "LONG" else -1), 5)


def _last_m15_close(source, snapshot):
    bars = (((source.get("frames") or {}).get("M15") or {})
            .get("last_3_completed_candles") or [])
    if not bars:
        return None
    candle = bars[-1]
    started = moment(candle.get("time"))
    if started is None:
        return None
    closed = started + timedelta(minutes=15)
    if closed > snapshot or snapshot - closed > timedelta(minutes=20):
        return None
    return candle.get("time")


def update_ledger(summary, existing=None):
    snapshot = moment(summary.get("collected_at"))
    if not snapshot:
        raise ValueError("OANDA summary collection time unavailable")
    book = existing if isinstance(existing, dict) else {}
    rows = book.get("opportunities") or {}
    if not isinstance(rows, dict):
        raise ValueError("Malformed opportunity ledger")
    pairs = summary.get("pairs") or {}

    # First observe already-registered immutable proposals. Future quotes
    # cannot rewrite their direction, entry region, stop, TP or first_seen.
    for row in rows.values():
        source = pairs.get(row.get("instrument")) or {}
        quote = _quote(source, snapshot)
        if not quote:
            row["tracking_status"] = "QUOTE_UNAVAILABLE_OR_STALE"
            continue
        first = moment(row.get("first_seen_at"))
        if not first or quote["at"] <= first:
            continue
        previous = moment(row.get("latest_quote_at"))
        if previous and quote["at"] <= previous:
            continue
        move = _move_pct(row, quote)
        if move is None:
            continue
        row["latest_quote_at"] = quote["at"].isoformat()
        row["latest_exit_side"] = round(
            quote["bid"] if row["direction"] == "LONG" else quote["ask"], 6)
        row["hypothetical_move_pct"] = move
        row["best_observed_pct"] = max(number(row.get("best_observed_pct")) or move, move)
        row["worst_observed_pct"] = min(number(row.get("worst_observed_pct")) or move, move)
        observations = row.setdefault("observations", [])
        observations.append({"at": quote["at"].isoformat(), "move_pct": move})
        row["observations"] = observations[-16:]
        expires = moment(row.get("expires_at"))
        if expires and quote["at"] > expires:
            row["tracking_status"] = "EXPIRED_RESEARCH_NO_VERIFIED_FILL"
        else:
            row["tracking_status"] = "WATCH_ONLY_UNTRIGGERED"
            threshold = number(row.get("meaningful_move_threshold_pct")) or 0.05
            if move >= threshold:
                row["tracking_status"] = "FAVOURABLE_MOVE_WITHOUT_VERIFIED_ENTRY"

    # Register only levels which the research engine could see NOW, not
    # retrospectively from completed candles inside a prior hourly interval.
    # No program may upgrade a watch into a canonical signals.json entry.
    for setup in build_research_setups(summary, limit=24):
        pair, route = setup.get("instrument"), setup.get("entry_route")
        if route not in ROUTES or setup.get("approved_entry") is not False:
            continue
        source = pairs.get(pair) or {}
        quote = _quote(source, snapshot)
        impulse_start = _last_m15_close(source, snapshot)
        if not quote or not impulse_start:
            continue
        if route == "MOMENTUM_CONTINUATION":
            # An M15 impulse cannot be registered before its close.
            if setup.get("impulse_m15_started_at") != impulse_start:
                continue
        direction = setup.get("direction")
        if direction not in ("LONG", "SHORT"):
            continue
        mid = (quote["bid"] + quote["ask"]) / 2
        atr = number((((source.get("frames") or {}).get("M15") or {})
                      .get("indicators") or {}).get("atr14"))
        if atr is None or atr <= 0:
            continue
        # Stable per-completed-M15 identifier. Repeated collector runs cannot
        # inflate opportunities, even if the hourly rank changes.
        candle_key = "".join(c for c in impulse_start if c.isdigit())[:12]
        ident = f"{candle_key}-{pair.replace('_', '')}-{direction}-{route}"
        if ident in rows:
            continue
        first_seen = quote["at"]
        ttl = timedelta(minutes=90 if route == "MOMENTUM_CONTINUATION" else 240)
        threshold_pct = round(100 * .6 * atr / mid, 5)
        rows[ident] = {
            "id": ident, "instrument": pair, "direction": direction,
            "route": route, "first_seen_at": first_seen.isoformat(),
            "m15_impulse_started_at": impulse_start,
            "expires_at": (first_seen + ttl).isoformat(),
            "entry_low": setup["entry_low"], "entry_high": setup["entry_high"],
            "stop": setup["stop"], "tp1": setup["tp1"], "tp2": setup.get("tp2"),
            "reference_mid": round(mid, 6),
            "meaningful_move_threshold_pct": threshold_pct,
            "latest_quote_at": first_seen.isoformat(),
            "latest_exit_side": round(
                quote["bid"] if direction == "LONG" else quote["ask"], 6),
            "hypothetical_move_pct": None,
            "best_observed_pct": None, "worst_observed_pct": None,
            "tracking_status": "WATCH_ONLY_UNTRIGGERED",
            "entry_confirmed": False, "broker_verified": False,
            "scored": False, "observations": [],
        }

    groups = {}
    for row in rows.values():
        group = groups.setdefault(row.get("route"), {
            "registered": 0, "favourable_unfilled": 0, "expired": 0})
        group["registered"] += 1
        if row.get("tracking_status") == "FAVOURABLE_MOVE_WITHOUT_VERIFIED_ENTRY":
            group["favourable_unfilled"] += 1
        if row.get("tracking_status") == "EXPIRED_RESEARCH_NO_VERIFIED_FILL":
            group["expired"] += 1
    return {
        "schema": 1, "updated_at": snapshot.isoformat(),
        "method": "Forward-only OANDA directional observations. No fills, TP/SL scoring, verified edge or account profit.",
        "routes": groups, "total_registered": len(rows), "opportunities": rows,
    }


def public_view(book, limit=60):
    """Small explicit field allowlist inside existing public learning block."""
    rows = list(((book or {}).get("opportunities") or {}).values())
    rows.sort(key=lambda x: (x.get("first_seen_at") or "", x.get("id") or ""))
    def flags(row):
        threshold = number(row.get("meaningful_move_threshold_pct"))
        best = number(row.get("best_observed_pct"))
        worst = number(row.get("worst_observed_pct"))
        favourable = (threshold is not None and threshold > 0
                      and best is not None and best >= threshold)
        adverse = (threshold is not None and threshold > 0
                   and worst is not None and worst <= -threshold)
        return favourable, adverse

    fields = ("id", "instrument", "direction", "route", "first_seen_at",
              "expires_at", "entry_low", "entry_high", "stop", "tp1", "tp2",
              "hypothetical_move_pct", "best_observed_pct", "worst_observed_pct",
              "meaningful_move_threshold_pct", "tracking_status",
              "entry_confirmed", "scored", "latest_quote_at")
    favourable = sum(flags(row)[0] for row in rows)
    adverse = sum(flags(row)[1] for row in rows)
    both = sum(all(flags(row)) for row in rows)
    return {
        "updated_at": book.get("updated_at"),
        "total_registered": len(rows),
        "routes": book.get("routes") or {},
        "lifecycle_audit": {
            "forward_watches": len(rows),
            "ever_meaningful_favourable": favourable,
            "ever_meaningful_adverse": adverse,
            "both_favourable_and_adverse": both,
            "expired_without_verified_fill": sum(
                row.get("tracking_status") == "EXPIRED_RESEARCH_NO_VERIFIED_FILL"
                for row in rows),
            "verified_fills": 0,
            "interpretation": "Overlapping directional observations, NOT historical wins, executable fills or probabilities; all watch and adverse outcomes retained."
        },
        "recent": [{**{key: row.get(key) for key in fields},
                    "ever_meaningful_favourable": flags(row)[0],
                    "ever_meaningful_adverse": flags(row)[1],
                    "observations": [
                        {"at": item.get("at"), "move_pct": item.get("move_pct")}
                        for item in (row.get("observations") or [])[-8:]
                        if isinstance(item, dict) and item.get("at") is not None
                    ]}
                   for row in rows[-limit:]],
        "disclaimer": "WATCH ONLY. Hypothetical direction is not a filled trade or profit.",
    }


def main():
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    existing = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    book = update_ledger(summary, existing)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(book, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Forward FX entry opportunities:", book["total_registered"],
          "route statistics:", book["routes"])


if __name__ == "__main__":
    main()

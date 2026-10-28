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
    from scripts.entry_readiness import entry_diagnostics
except ModuleNotFoundError:  # direct execution: python scripts/opportunity_tracker.py
    from export_public_dashboard import build_research_setups
    from entry_readiness import entry_diagnostics

SUMMARY = Path("data/summary.json")
LEDGER = Path("data/entry_opportunity_ledger.json")
NEWS = Path("data/news_latest.json")
ROUTES = ("MOMENTUM_CONTINUATION", "EMA20_PULLBACK", "M15_STRUCTURAL_BREAK")


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


def _forward_entry_pattern(row, source, snapshot):
    """Observe an independent completed post-registration candle, never a fill.

    The entire bar must OPEN after the original watch was recorded: a bar
    which was already forming at publication could have touched the entry
    level before we knew about the watch and cannot validate a forward
    signal. The candidate price path remains research-only.
    """
    route = row.get("route")
    period = "M5" if route == "MOMENTUM_CONTINUATION" else "M15"
    minutes = 5 if period == "M5" else 15
    candles = (((source.get("frames") or {}).get(period) or {})
               .get("last_3_completed_candles") or [])
    first, expires = moment(row.get("first_seen_at")), moment(row.get("expires_at"))
    low, high, stop = (number(row.get(k)) for k in ("entry_low", "entry_high", "stop"))
    if not first or None in (low, high, stop) or low <= 0 or high < low:
        return None
    direction = row.get("direction")
    qualified = []
    for i, candle in enumerate(candles):
        opening = moment(candle.get("time"))
        if not opening:
            continue
        ending = opening + timedelta(minutes=minutes)
        if opening <= first or ending > snapshot + timedelta(seconds=1):
            continue
        if expires and ending > expires:
            continue
        mid = candle.get("mid") or {}
        values = [number(mid.get(k)) for k in ("o", "h", "l", "c")]
        if any(v is None for v in values):
            continue
        o, h, l, c = values
        if not 0 < l <= min(o, c) <= max(o, c) <= h:
            continue
        span = h - l
        if span <= 0:
            continue
        in_zone = l <= high and h >= low
        up, down = direction == "LONG", direction == "SHORT"
        # If the intrabar high/low crosses the proposed stop, the ordering of
        # a reversal and an invalidation is unknown; no valid pattern claim.
        stop_touched = (up and l <= stop) or (down and h >= stop)
        if stop_touched:
            continue
        if route == "EMA20_PULLBACK":
            # Research rejection: candle tests the frozen band and ends with
            # a directional body beyond the far edge of that band.
            ok = (in_zone and (
                (up and c > o and c >= high and (c-l)/span >= .60) or
                (down and c < o and c <= low and (h-c)/span >= .60)))
        elif route == "M15_STRUCTURAL_BREAK":
            # Fully independent M15 bar, published BEFORE the bar opened.
            # A later price break near the frozen band is a PAPER PATTERN,
            # not an order fill, approval or proven edge.
            ok = in_zone and ((up and c > o and c >= high and
                               (c-l)/span >= .60) or
                              (down and c < o and c <= low and
                               (h-c)/span >= .60))
        elif route == "MOMENTUM_CONTINUATION":
            if i == 0:
                continue  # no earlier fully observed comparator in this slice
            prior = (candles[i-1].get("mid") or {})
            ph, pl = number(prior.get("h")), number(prior.get("l"))
            if None in (ph, pl):
                continue
            # Strong M5 continuation near the frozen, prospective entry
            # range; no retrospective impulse fill and no chasing a gap.
            ok = (in_zone and abs(c-o)/span >= .50 and (
                (up and c > o and c > ph and (c-l)/span >= .70) or
                (down and c < o and c < pl and (h-c)/span >= .70)))
        else:
            ok = False
        if ok:
            qualified.append((ending, c, period))
    if not qualified:
        return None
    ended, close, period = min(qualified, key=lambda x: x[0])
    return {"entry_pattern_status": "FORWARD_CANDLE_OBSERVED_PAPER_ONLY",
            "entry_pattern_at": ended.isoformat(),
            "entry_pattern_close": round(close, 6),
            "entry_pattern_timeframe": period}


def update_ledger(summary, existing=None, news=None):
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
        # Inspect the independent forward bar without claiming a fill,
        # broker executable price, approved alert, or profitable strategy.
        if row.get("entry_pattern_status") != "FORWARD_CANDLE_OBSERVED_PAPER_ONLY":
            pattern = _forward_entry_pattern(row, source, snapshot)
            if pattern:
                row.update(pattern)
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
            if row.get("entry_pattern_status") == "FORWARD_CANDLE_OBSERVED_PAPER_ONLY":
                row["tracking_status"] = "FORWARD_PATTERN_OBSERVED_NO_APPROVED_FILL"

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
            "entry_pattern_status": "AWAITING_INDEPENDENT_POST_PLAN_CANDLE",
            "entry_pattern_at": None, "entry_pattern_close": None,
            "entry_confirmed": False, "broker_verified": False,
            "scored": False, "observations": [],
        }

    # Independently risk-priced structural breakout studies are a separate
    # challenger route. Record their original entry, swing stop, structural
    # target and FUTURE M15 trigger before any subsequent candle opens.
    # No promotion into signals.json; not an approved or executed trade.
    for diag in entry_diagnostics(summary, news or {"status": "PARTIAL"}, limit=12):
        preview = diag.get("priced_paper_preview")
        if not isinstance(preview, dict) or preview.get("approved_entry") is not False:
            continue
        pair, direction = diag.get("instrument"), diag.get("direction")
        source = pairs.get(pair) or {}
        quote = _quote(source, snapshot)
        impulse_start = _last_m15_close(source, snapshot)
        if not quote or not impulse_start or direction not in ("LONG", "SHORT"):
            continue
        expire = moment(preview.get("expires_at"))
        if not expire or expire <= quote["at"]:
            continue
        low, high = number(preview.get("entry_zone_low")), number(preview.get("entry_zone_high"))
        stop, tp1 = number(preview.get("stop")), number(preview.get("tp1"))
        if None in (low, high, stop, tp1) or not (0 < low < high and stop > 0 and tp1 > 0):
            continue
        mid = (quote["bid"] + quote["ask"]) / 2
        atr = number((((source.get("frames") or {}).get("M15") or {})
                      .get("indicators") or {}).get("atr14"))
        if not atr or atr <= 0:
            continue
        candle_key = "".join(c for c in impulse_start if c.isdigit())[:12]
        route = "M15_STRUCTURAL_BREAK"
        ident = f"{candle_key}-{pair.replace('_', '')}-{direction}-{route}"
        if ident in rows:
            continue
        first_seen = quote["at"]
        rows[ident] = {
            "id": ident, "instrument": pair, "direction": direction,
            "route": route, "first_seen_at": first_seen.isoformat(),
            "m15_impulse_started_at": impulse_start,
            "expires_at": expire.isoformat(),
            "entry_low": low, "entry_high": high,
            "stop": stop, "tp1": tp1, "tp2": None,
            "original_trigger": "New completed M15 close beyond frozen prior candle high/low",
            "entry_plan_class": "PRICED_PAPER_RESEARCH_NOT_TRIGGERED",
            "reference_mid": round(mid, 6),
            "meaningful_move_threshold_pct": round(100 * .6 * atr / mid, 5),
            "latest_quote_at": first_seen.isoformat(),
            "latest_exit_side": round(
                quote["bid"] if direction == "LONG" else quote["ask"], 6),
            "hypothetical_move_pct": None,
            "best_observed_pct": None, "worst_observed_pct": None,
            "tracking_status": "WATCH_ONLY_UNTRIGGERED",
            "entry_pattern_status": "AWAITING_INDEPENDENT_POST_PLAN_CANDLE",
            "entry_pattern_at": None, "entry_pattern_close": None,
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
        "method": "Forward-only OANDA directional observations and independently completed entry-pattern checks. Entry patterns are paper research; no fills, TP/SL scoring, verified edge or account profit.",
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
              "entry_confirmed", "scored", "latest_quote_at",
              "entry_pattern_status", "entry_pattern_at", "entry_pattern_close")
    favourable = sum(flags(row)[0] for row in rows)
    adverse = sum(flags(row)[1] for row in rows)
    both = sum(all(flags(row)) for row in rows)
    # Compare the actual *prospective* routes, without turning favourable
    # direction into a filled trade. All denominators include expired and
    # untestable watches, not merely successful patterns.
    route_study = {}
    for row in rows:
        route = row.get("route")
        if route not in ROUTES:
            continue
        item = route_study.setdefault(route, {
            "registered": 0, "forward_patterns": 0, "expired_without_pattern": 0,
            "ever_favourable": 0, "ever_adverse": 0, "both_directions": 0,
            "observed_pattern_delay_minutes": [],
        })
        item["registered"] += 1
        good, bad = flags(row)
        item["ever_favourable"] += int(good)
        item["ever_adverse"] += int(bad)
        item["both_directions"] += int(good and bad)
        pattern = row.get("entry_pattern_status") == "FORWARD_CANDLE_OBSERVED_PAPER_ONLY"
        item["forward_patterns"] += int(pattern)
        item["expired_without_pattern"] += int(
            not pattern and row.get("tracking_status") == "EXPIRED_RESEARCH_NO_VERIFIED_FILL")
        if pattern:
            first, observed = moment(row.get("first_seen_at")), moment(row.get("entry_pattern_at"))
            if first and observed and observed >= first:
                item["observed_pattern_delay_minutes"].append(
                    round((observed - first).total_seconds() / 60, 2))
    for item in route_study.values():
        delays = sorted(item.pop("observed_pattern_delay_minutes"))
        count = len(delays)
        item["median_pattern_delay_minutes"] = (
            round((delays[(count-1)//2] + delays[count//2]) / 2, 2) if count else None)
        item["validated_trading_edge"] = False

    # Head-to-head only when both immutable routes were registered for the
    # same completed M15 reference bar, pair and direction. Never compare a
    # later watch against an earlier market move as though simultaneous.
    paired = {}
    for row in rows:
        if row.get("route") not in ("EMA20_PULLBACK", "MOMENTUM_CONTINUATION"):
            continue
        key = (row.get("instrument"), row.get("direction"),
               row.get("m15_impulse_started_at"))
        if not all(key):
            continue
        paired.setdefault(key, {})[row["route"]] = row
    head_to_head = {"paired_cohorts": 0, "m5_earlier": 0,
                    "m15_earlier": 0, "same_time": 0,
                    "m5_pattern_only": 0, "m15_pattern_only": 0,
                    "neither_pattern": 0, "paper_only": True}
    for group in paired.values():
        if not all(k in group for k in ("EMA20_PULLBACK", "MOMENTUM_CONTINUATION")):
            continue
        head_to_head["paired_cohorts"] += 1
        m5 = moment(group["MOMENTUM_CONTINUATION"].get("entry_pattern_at")) if (
            group["MOMENTUM_CONTINUATION"].get("entry_pattern_status") ==
            "FORWARD_CANDLE_OBSERVED_PAPER_ONLY") else None
        m15 = moment(group["EMA20_PULLBACK"].get("entry_pattern_at")) if (
            group["EMA20_PULLBACK"].get("entry_pattern_status") ==
            "FORWARD_CANDLE_OBSERVED_PAPER_ONLY") else None
        field = ("m5_earlier" if m5 < m15 else "m15_earlier" if m15 < m5 else "same_time") if (
            m5 and m15) else ("m5_pattern_only" if m5 else
                             "m15_pattern_only" if m15 else "neither_pattern")
        head_to_head[field] += 1

    # Compact index of EVERY registered route watch. The rich per-candidate
    # history may still be a rolling window to meet the 930 KB feed budget.
    index_fields = ("id", "instrument", "direction", "route",
                    "entry_low", "entry_high", "stop", "tp1",
                    "hypothetical_move_pct", "tracking_status")
    return {
        "updated_at": book.get("updated_at"),
        "total_registered": len(rows),
        "routes": book.get("routes") or {},
        "route_study": route_study,
        "head_to_head": head_to_head,
        "candidate_index_fields": list(index_fields),
        "candidate_index": [[row.get(k) for k in index_fields] for row in rows],
        "public_window": {"detailed_rows": min(limit, len(rows)),
                          "total_rows": len(rows),
                          "truncated": len(rows) > limit},
        "lifecycle_audit": {
            "forward_watches": len(rows),
            "ever_meaningful_favourable": favourable,
            "ever_meaningful_adverse": adverse,
            "both_favourable_and_adverse": both,
            "expired_without_verified_fill": sum(
                row.get("tracking_status") == "EXPIRED_RESEARCH_NO_VERIFIED_FILL"
                for row in rows),
            "verified_fills": 0,
            "forward_entry_patterns_observed": sum(
                row.get("entry_pattern_status") == "FORWARD_CANDLE_OBSERVED_PAPER_ONLY"
                for row in rows),
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
    news = json.loads(NEWS.read_text(encoding="utf-8")) if NEWS.exists() else {"status": "PARTIAL"}
    book = update_ledger(summary, existing, news)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(book, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Forward FX entry opportunities:", book["total_registered"],
          "route statistics:", book["routes"])


if __name__ == "__main__":
    main()

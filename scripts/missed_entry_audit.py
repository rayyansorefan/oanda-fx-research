"""Forward-only missed FX research entry audit using OANDA completed bid/ask bars.

This is paper research, never Pepperstone execution. A pattern isn't a fill.
Every result is tied to an immutable, originally registered entry zone, stop,
target, pattern time and full OHLC evidence. Missing data fails closed.
"""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from scripts.paper_trade_audit import source_bars, iso
except ModuleNotFoundError:
    from paper_trade_audit import source_bars, iso

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
MAX_REMOTE_REPLAYS = 18
TERMINAL = {
    "PAPER_TP1_AFTER_FILL", "PAPER_STOP_AFTER_FILL",
    "PAPER_AMBIGUOUS_FILL_BAR", "PAPER_AMBIGUOUS_EXIT_BAR",
    "PAPER_PRE_ENTRY_INVALIDATED", "PAPER_NOT_FILLED_BEFORE_EXPIRY",
    "PAPER_NO_CONTIGUOUS_CANDLES", "PAPER_BAD_ORIGINAL_TERMS",
    "PAPER_TIME_EXIT",
}

def when(x):
    try:
        t = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return t.astimezone(UTC) if t.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None

def number(x):
    try:
        val = float(x)
        return val if -1e20 < val < 1e20 else None
    except (TypeError, ValueError, OverflowError):
        return None

def outcome(row, candles, now):
    """Simulate conditional entry only AFTER independently completed signal bar.

    Long limit = original entry zone HIGH (worst reasonable buy); short limit
    = zone LOW (worst reasonable sell). Broker spread-bet fill is UNKNOWN.
    """
    created, pattern, expires = (
        when(row.get("first_seen_at")), when(row.get("entry_pattern_at")),
        when(row.get("expires_at")))
    prices = [number(row.get(k)) for k in ("entry_low", "entry_high", "stop", "tp1")]
    key = {"id": row.get("id"), "instrument": row.get("instrument"),
           "direction": row.get("direction"), "route": row.get("route"),
           "pattern_at": row.get("entry_pattern_at"),
           "broker_verified": False, "trade_executed": False}
    if (not created or not pattern or not expires or
            row.get("entry_pattern_status") != "FORWARD_CANDLE_OBSERVED_PAPER_ONLY" or
            not created < pattern <= expires or None in prices):
        return {**key, "status": "PAPER_BAD_ORIGINAL_TERMS", "scored": False}
    lo, hi, stop, tp = prices
    long = row.get("direction") == "LONG"
    if not 0 < lo <= hi or not (
        stop < lo < hi < tp if long else tp < lo < hi < stop):
        return {**key, "status": "PAPER_BAD_ORIGINAL_TERMS", "scored": False}
    entry = hi if long else lo
    # Confirm the original thesis had not ALREADY hit its frozen stop
    # before the entry-pattern close. Ignore the bar forming when the plan
    # first appeared; all pre-pattern checks use entirely subsequent bars.
    first_bar = (created - dt.timedelta(minutes=created.minute % 15,
                                        seconds=created.second,
                                        microseconds=created.microsecond)
                 + dt.timedelta(minutes=15))
    earlier = sorted((b for b in candles if b.get("t") and
                      first_bar <= b["t"] < pattern and
                      b["t"] + dt.timedelta(minutes=15) <= now),
                     key=lambda b: b["t"])
    if not earlier or earlier[0]["t"] != first_bar:
        return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES",
                "reason": "Original post-recording history missing", "scored": False}
    expected = first_bar
    for bar in earlier:
        if bar["t"] != expected:
            return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES",
                    "reason": "Gap before the confirmed pattern", "scored": False}
        expected += dt.timedelta(minutes=15)
        quote = bar["bid"] if long else bar["ask"]
        if (quote["l"] <= stop if long else quote["h"] >= stop):
            return {**key, "status": "PAPER_PRE_ENTRY_INVALIDATED",
                    "invalidated_at": iso(bar["t"]+dt.timedelta(minutes=15)),
                    "scored": False,
                    "reason": "Frozen stop breached before pattern confirmation"}
    if expected != pattern:
        return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES",
                "reason": "Pre-pattern candles do not reach signal close",
                "scored": False}
    # Only strictly subsequent complete M15 candles qualify; the pattern
    # candle itself can never be treated as an entry.
    eligible = sorted((b for b in candles if b.get("t") and
                       b["t"] >= pattern and
                       b["t"] + dt.timedelta(minutes=15) <= now),
                      key=lambda b: b["t"])
    if not eligible:
        return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES", "scored": False}
    if eligible[0]["t"] > pattern:
        return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES",
                "reason": "First post-confirmation candle absent", "scored": False}
    position = False
    last = None
    for bar in eligible:
        start = bar["t"]
        if last is not None and start-last > dt.timedelta(minutes=15, seconds=1):
            return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES",
                    "reason": "Missing intervening complete M15 bar", "scored": False}
        last = start
        if not position and start >= expires:
            break
        bid, ask = bar["bid"], bar["ask"]
        if any(number((bid if side == "bid" else ask).get(k)) is None
               for side in ("bid", "ask") for k in ("o", "h", "l", "c")):
            return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES",
                    "reason": "Invalid bid/ask price data", "scored": False}
        if any(bid[k] > ask[k] for k in ("o", "h", "l", "c")):
            return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES",
                    "reason": "Bid exceeds ask", "scored": False}
        side = ask if long else bid
        exit_side = bid if long else ask
        stop_hit = (exit_side["l"] <= stop if long else exit_side["h"] >= stop)
        target_hit = (exit_side["h"] >= tp if long else exit_side["l"] <= tp)
        if not position:
            # The price must actually trade through the original worst-case
            # limit. Never assume a touch in the earlier pattern bar filled.
            hit = side["l"] <= entry <= side["h"]
            if hit and (stop_hit or target_hit):
                return {**key, "status": "PAPER_AMBIGUOUS_FILL_BAR",
                        "fill_at": iso(start), "scored": False,
                        "reason": "Fill and exit level appear in same OHLC bar"}
            if stop_hit:
                return {**key, "status": "PAPER_PRE_ENTRY_INVALIDATED",
                        "invalidated_at": iso(start), "scored": False}
            if hit:
                position = True
                filled_at = start
            continue
        if stop_hit and target_hit:
            return {**key, "status": "PAPER_AMBIGUOUS_EXIT_BAR",
                    "fill_at": iso(filled_at), "exit_at": iso(start), "scored": False}
        if stop_hit or target_hit:
            exit_price = stop if stop_hit else tp
            pnl = (exit_price-entry)/entry*100*(1 if long else -1)
            return {**key, "status": "PAPER_STOP_AFTER_FILL" if stop_hit
                    else "PAPER_TP1_AFTER_FILL", "scored": True,
                    "fill_at": iso(filled_at), "exit_at": iso(start),
                    "paper_entry": entry, "paper_exit": exit_price,
                    "underlying_move_pct": round(pnl, 5)}
        if start >= filled_at + dt.timedelta(hours=24):
            px = exit_side["o"]
            pnl = (px-entry)/entry*100*(1 if long else -1)
            return {**key, "status": "PAPER_TIME_EXIT", "scored": True,
                    "fill_at": iso(filled_at), "exit_at": iso(start),
                    "paper_entry": entry, "paper_exit": px,
                    "underlying_move_pct": round(pnl, 5)}
    if position:
        return {**key, "status": "PAPER_OPEN_UNRESOLVED", "scored": False,
                "fill_at": iso(filled_at), "paper_entry": entry}
    if now >= expires and (
            last is None or last + dt.timedelta(minutes=15) < expires):
        return {**key, "status": "PAPER_NO_CONTIGUOUS_CANDLES",
                "reason": "Final pre-expiry M15 candle not yet observed",
                "scored": False}
    return {**key, "status": ("PAPER_NOT_FILLED_BEFORE_EXPIRY"
                           if now >= expires else "PAPER_WAITING_FOR_ENTRY"),
            "scored": False}

def audit(book, prior=None, now=None, fetch=None):
    now = now or dt.datetime.now(UTC)
    entries = ((book or {}).get("opportunities") or {})
    old = (prior or {}).get("results") or {}
    results = dict(old)
    queue = sorted((v for v in entries.values()
                    if v.get("entry_pattern_status") == "FORWARD_CANDLE_OBSERVED_PAPER_ONLY"
                    and v.get("id") not in results),
                   key=lambda v: (v.get("first_seen_at") or "", v.get("id") or ""))
    for item in queue[:MAX_REMOTE_REPLAYS]:
        try:
            if fetch is None:
                # Existing OANDA GET-only fetcher; secrets never persisted.
                frozen = {"instrument": item["instrument"],
                          "created_at": item["first_seen_at"],
                          "expires_at": item["expires_at"]}
                candles = source_bars(frozen, now)
            else:
                candles = fetch(item, now)
            value = outcome(item, candles, now)
        except (RuntimeError, OSError, ValueError, TimeoutError, TypeError, KeyError) as exc:
            value = {"id": item.get("id"), "status": "PAPER_SOURCE_UNAVAILABLE",
                     "reason": type(exc).__name__, "scored": False,
                     "broker_verified": False, "trade_executed": False}
        if value["status"] in TERMINAL:
            results[item["id"]] = value
        else:
            # Do not persist missing candle history as a terminal outcome.
            results[item["id"]] = value
    # Re-evaluate unresolved cases in later runs rather than freezing an
    # open hypothetical position as a trade result.
    retry = [v for v in entries.values() if v.get("id") in results and
             results[v["id"]].get("status") in
             ("PAPER_OPEN_UNRESOLVED", "PAPER_WAITING_FOR_ENTRY",
              "PAPER_NO_CONTIGUOUS_CANDLES", "PAPER_SOURCE_UNAVAILABLE")]
    for item in retry[:max(0, MAX_REMOTE_REPLAYS-len(queue[:MAX_REMOTE_REPLAYS]))]:
        try:
            candles = (fetch(item, now) if fetch else source_bars({
                "instrument": item["instrument"], "created_at": item["first_seen_at"],
                "expires_at": item["expires_at"]}, now))
            results[item["id"]] = outcome(item, candles, now)
        except (RuntimeError, OSError, ValueError, TimeoutError, TypeError, KeyError):
            pass
    session_day = now.astimezone(ZoneInfo("Europe/London")).date().isoformat()
    today = [v for v in entries.values() if
             when(v.get("first_seen_at")) and
             when(v["first_seen_at"]).astimezone(
                 ZoneInfo("Europe/London")).date().isoformat() == session_day]
    flagged = [v for v in today if
               v.get("entry_pattern_status") == "FORWARD_CANDLE_OBSERVED_PAPER_ONLY"]
    distinct = {(r.get("instrument"), r.get("direction"), r.get("entry_pattern_at"))
                for r in flagged}
    score = [results[r["id"]] for r in flagged if r.get("id") in results]
    counts = {}
    for r in score:
        counts[r.get("status")] = counts.get(r.get("status"), 0)+1
    return {
        "schema": 1, "updated_at": iso(now),
        "policy": ("Prospectively logged research patterns; no broker orders. "
                   "M15 bid/ask replay AFTER confirmation with frozen worst entry "
                   "zone and fixed stop/TP. Ambiguity and gaps never count as wins."),
        "today_uk": session_day,
        "today_registered": len(today),
        "today_forward_patterns": len(flagged),
        "today_distinct_pair_pattern_events": len(distinct),
        "today_scored_hypothetical_entries": sum(v.get("scored") is True for v in score),
        "today_status_counts": counts,
        "historical_results": results,
    }

def main():
    ledger = json.loads((ROOT / "data/entry_opportunity_ledger.json").read_text())
    file = ROOT / "data/missed_entry_audit.json"
    previous = json.loads(file.read_text()) if file.exists() else {}
    result = audit(ledger, previous)
    file.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print("Missed FX opportunity verification", {
        "registered": result["today_registered"],
        "forward_patterns": result["today_forward_patterns"],
        "unique_events": result["today_distinct_pair_pattern_events"],
        "scored_paper": result["today_scored_hypothetical_entries"],
        "outcomes": result["today_status_counts"]})

if __name__ == "__main__":
    main()

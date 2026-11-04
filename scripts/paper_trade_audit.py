#!/usr/bin/env python3
"""Independent, append-only OANDA FX paper-signal evaluator. NEVER places orders.

Formal shortlist plans are frozen on first sight. OANDA M15 bid/ask candles are
replayed strictly after each plan's creation. An unconfirmed idea is NOT a trade;
same-bar ordering ambiguity and incomplete data are NEVER counted as wins.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PLANS = Path("paper-trade-candidates.json")
LIVE = Path("signals.json")
OUT = Path("data/paper_trade_ledger.json")
API = "https://api-fxpractice.oanda.com"
MAX_CANDIDATES = 5000
MAX_HOLD_BARS = 96  # 24 trading hours; pauses through weekend market closure.
MAX_REMOTE_CALLS = 25
UTC = dt.timezone.utc


def utc_now():
    return dt.datetime.now(UTC)


def stamp(value):
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone-aware timestamps required")
    return parsed.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def numeric(value):
    n = float(value)
    if not math.isfinite(n):
        raise ValueError("Non-finite price")
    return n


def positive_price(value):
    try:
        number = numeric(value)
        return number if number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def validate(plan):
    required = ("id", "instrument", "direction", "created_at", "expires_at",
                "planned_entry", "stop", "tp1", "max_oanda_spread_pips")
    if any(plan.get(k) is None for k in required):
        return "ORIGINAL_LEVELS_OR_TRIGGER_MISSING"
    if all(plan.get(x) is None for x in ("alert_a", "alert_b", "alert_c")):
        return "UNSUPPORTED_OR_MISSING_ORIGINAL_ENTRY_ROUTE"
    if plan.get("alert_c") is not None:
        ctx = plan.get("technical_context") or {}
        needed = ("m15_adx", "h1_adx", "m15_plus_di", "m15_minus_di",
                  "h1_plus_di", "h1_minus_di", "m15_atr")
        if (ctx.get("full_frame_alignment") is not True and ctx.get("admission_policy") != "challenger_v1") or any(
                numeric(ctx.get(x)) is None for x in needed):
            return "CONTINUATION_ORIGINAL_FRAME_EVIDENCE_MISSING"
    try:
        key = plan["instrument"]
        if len(key) != 7 or key[3] != "_" or not key.replace("_", "").isalpha():
            return "INVALID_INSTRUMENT"
        if plan["direction"] not in ("LONG", "SHORT"):
            return "INVALID_DIRECTION"
        if stamp(plan["expires_at"]) <= stamp(plan["created_at"]):
            return "INVALID_EXPIRY"
        en, sl, tp = (numeric(plan[k]) for k in ("planned_entry", "stop", "tp1"))
        if not (sl < en < tp if plan["direction"] == "LONG" else tp < en < sl):
            return "INVALID_RISK_GEOMETRY"
        if numeric(plan["max_oanda_spread_pips"]) <= 0:
            return "INVALID_SPREAD_CAP"
        for key in ("alert_a", "alert_b", "alert_c"):
            if plan.get(key) is not None and numeric(plan[key]) <= 0:
                return "INVALID_ALERT_LEVEL"
    except (TypeError, ValueError, OverflowError, KeyError):
        return "INVALID_PLAN"
    return None


def frozen(plan):
    """No revisions of prices/time/trigger under the same ID."""
    fields = ("id", "instrument", "direction", "created_at", "expires_at",
              "planned_entry", "entry_low", "entry_high", "stop", "tp1", "tp2",
              "alert_a", "alert_b", "alert_c",
              "max_oanda_spread_pips", "event_blackouts_utc", "source",
              "source_ref", "original_note", "strategy", "technical_context", "pip_size")
    return {k: plan[k] for k in fields if k in plan}


def economic_terms(plan):
    """Compare immutable economics, not optional provenance/technical commentary."""
    names = ("instrument", "direction", "created_at", "expires_at",
             "planned_entry", "stop", "tp1", "tp2", "alert_a", "alert_b", "alert_c",
             "max_oanda_spread_pips", "event_blackouts_utc")
    return {k: plan.get(k, [] if k == "event_blackouts_utc" else None) for k in names}


def pip_size(plan):
    if plan.get("pip_size") is not None:
        value=numeric(plan["pip_size"])
        if value is None or value<=0:raise ValueError("Invalid frozen pip size")
        return value
    return 0.01 if plan["instrument"].endswith("_JPY") else 0.0001  # Legacy convention; source not backfilled.


def blackout(plan, when, opened_at=None):
    for window in plan.get("event_blackouts_utc") or []:
        if (stamp(window["start"]) < when and
                (opened_at if opened_at is not None else when) < stamp(window["end"])):
            return True
    return False


def ohlc(bar, side):
    return {key: numeric(bar[side][key]) for key in ("o", "h", "l", "c")}


def parse_bars(payload):
    bars = []
    for bar in payload.get("candles", []):
        if bar.get("complete") is not True:
            continue
        t = stamp(bar["time"])
        bid, ask = ohlc(bar, "bid"), ohlc(bar, "ask")
        mid = {k: (bid[k] + ask[k]) / 2 for k in ("o", "h", "l", "c")}
        if any(bid[k] > ask[k] for k in ("o", "h", "l", "c")):
            raise ValueError("Crossed OANDA bid/ask candle")
        if any(x["l"] > min(x["o"], x["c"]) or x["h"] < max(x["o"], x["c"])
               for x in (bid, ask)):
            raise ValueError("Invalid OANDA OHLC")
        bars.append({"t": t, "bid": bid, "ask": ask, "mid": mid})
    bars.sort(key=lambda x: x["t"])
    if len({b["t"] for b in bars}) != len(bars):
        raise ValueError("Duplicate candle time")
    return bars


class EvidenceBars(list):
    """Parsed candles with a credential-free persisted source reference."""
    pass

def source_bars(plan, when, fetch=None):
    created = stamp(plan["created_at"])
    expiry = stamp(plan["expires_at"])
    # A Friday position can legitimately reach its 96th M15 bar on Monday/Tuesday.
    through = min(when, expiry + dt.timedelta(days=5))
    start = created - dt.timedelta(minutes=30)
    if through <= start:
        return []
    if fetch:
        return fetch(plan, start, through)
    token = os.environ.get("OANDA_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("OANDA_API_TOKEN_UNAVAILABLE")
    query = urlencode({"from": iso(start), "to": iso(through), "granularity": "M15",
                       "price": "BA", "smooth": "false"})
    request = Request(API + "/v3/instruments/" + plan["instrument"] + "/candles?" + query,
                      headers={"Authorization": "Bearer " + token,
                               "Accept": "application/json",
                               "User-Agent": "fx-paper-audit-read-only"}, method="GET")
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    bars = parse_bars(payload)
    # Content-addressed evidence contains market candles only, never credentials.
    import hashlib
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    directory = Path("data/replay_evidence")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (plan["instrument"] + "-" + digest + ".json")
    if not path.exists():
        path.write_text(raw + "\n")
    result = EvidenceBars(bars)
    result.evidence_path = str(path)
    return result


def _eligible_trigger(plan, bar):
    """Completed M15 rejection; a wick or a guessed M5 confirmation is insufficient."""
    mid = bar["mid"]
    if plan.get("alert_b") is None:
        return False
    threshold = numeric(plan["alert_b"])
    entry = numeric(plan["planned_entry"])
    long = plan["direction"] == "LONG"
    touched = mid["l"] <= threshold if long else mid["h"] >= threshold
    direction = (mid["c"] > mid["o"] and mid["c"] >= entry) if long else (
        mid["c"] < mid["o"] and mid["c"] <= entry)
    spread = (bar["ask"]["c"] - bar["bid"]["c"]) / pip_size(plan)
    return touched and direction and spread <= numeric(plan["max_oanda_spread_pips"])


def _eligible_continuation(plan, bar, previous_close):
    """Forward-only completed M15 impulse; frozen original H1/M15 evidence."""
    if plan.get("alert_c") is None or previous_close is None:
        return False
    ctx = plan.get("technical_context") or {}
    long = plan["direction"] == "LONG"
    if (ctx.get("full_frame_alignment") is not True and ctx.get("admission_policy") != "challenger_v1") or any(
            numeric(ctx.get(key)) is None for key in
            ("m15_adx", "h1_adx", "m15_plus_di", "m15_minus_di",
             "h1_plus_di", "h1_minus_di")):
        return False
    minimum = ctx.get("momentum_adx_minimum", 22)
    if (ctx.get("admission_policy") != "challenger_v1" and numeric(ctx["m15_adx"]) < minimum) or numeric(ctx["h1_adx"]) < (18 if ctx.get("admission_policy") == "challenger_v1" else 20):
        return False
    for prefix in ("m15", "h1"):
        plus, minus = numeric(ctx[prefix+"_plus_di"]), numeric(ctx[prefix+"_minus_di"])
        if not (plus > minus if long else minus > plus):
            return False
    level = numeric(plan["alert_c"])
    mid = bar["mid"]
    span = mid["h"] - mid["l"]
    if span <= 0 or abs(mid["c"]-mid["o"]) / span < .55:
        return False
    pos = (mid["c"]-mid["l"]) / span if long else (mid["h"]-mid["c"]) / span
    if pos < .72:
        return False
    tolerance = 2 * pip_size(plan)
    if not ((previous_close <= level and mid["c"] > level+tolerance)
            if long else (previous_close >= level and mid["c"] < level-tolerance)):
        return False
    spread = (bar["ask"]["c"] - bar["bid"]["c"]) / pip_size(plan)
    return spread <= numeric(plan["max_oanda_spread_pips"])


def _stop_hit(plan, bar):
    stop = numeric(plan["stop"])
    return (bar["bid"]["l"] <= stop if plan["direction"] == "LONG"
            else bar["ask"]["h"] >= stop)


def _tp_hit(plan, bar):
    target = numeric(plan["tp1"])
    return (bar["bid"]["h"] >= target if plan["direction"] == "LONG"
            else bar["ask"]["l"] <= target)


def _limit_hit(plan, bar):
    entry = numeric(plan["planned_entry"])
    quote = bar["ask"] if plan["direction"] == "LONG" else bar["bid"]
    return quote["l"] <= entry <= quote["h"]


def _r(plan, price):
    entry, stop = numeric(plan["planned_entry"]), numeric(plan["stop"])
    sign = 1 if plan["direction"] == "LONG" else -1
    return round(sign * (price - entry) / abs(entry - stop), 4)


def directional_pct(plan, exit_side_price):
    """Underlying directional price move %, NOT percent of margin or account equity."""
    entry = numeric(plan["planned_entry"])
    sign = 1 if plan["direction"] == "LONG" else -1
    return round(sign * (numeric(exit_side_price) - entry) / entry * 100, 5)


def research_move_pct(direction, reference, exit_side_price):
    """Direction-adjusted spot movement, never a simulated trade return."""
    sign = 1 if direction == "LONG" else -1
    return round(sign * (numeric(exit_side_price) - numeric(reference)) /
                 numeric(reference) * 100, 5)


def update_price_tracking(book, market, now, candle_samples=None):
    """Monitor *every* shortlisted thesis, irrespective of audit eligibility.

    An exact original entry is the reference when available. Otherwise a preserved
    entry-zone midpoint is research-only; if the original idea had neither, the
    first *subsequent* observed quote is frozen as the forward monitoring baseline.
    These spot movements NEVER imply a filled order or reconstruct Friday returns.
    """
    candle_samples = candle_samples or {}
    pairs = (market or {}).get("pairs") or {}
    for rec in book.get("candidates", {}).values():
        plan, result = rec["plan"], rec.get("result") or {}
        if (result.get("scored") is True and result.get("exit") is not None
                and positive_price(plan.get("planned_entry")) is not None):
            result["exit_move_pct"] = directional_pct(plan, result["exit"])
        stage = result.get("status")
        prior = rec.get("tracking") or {}
        history = list(prior.get("history") or [])
        has_entry = positive_price(plan.get("planned_entry")) is not None
        try:
            if has_entry and plan.get("created_at") and plan.get("expires_at"):
                expires, created = stamp(plan["expires_at"]), stamp(plan["created_at"])
                close_side = "bid" if plan["direction"] == "LONG" else "ask"
                for b in candle_samples.get(plan["id"], []):
                    closes = b["t"] + dt.timedelta(minutes=15)
                    if closes.minute != 0 or not created < closes <= now:
                        continue
                    if closes > expires and stage != "OPEN":
                        continue
                    history.append({"at": iso(closes),
                                    "price": b[close_side]["c"],
                                    "move_pct": directional_pct(plan, b[close_side]["c"]),
                                    "source": "COMPLETED_M15_CLOSE"})
            row = pairs.get(plan["instrument"]) or {}
            quote = row.get("quote") or {}
            quote_at = stamp(quote["time"])
            bid, ask = numeric(quote["bid"]), numeric(quote["ask"])
            if not (0 < bid <= ask and quote.get("tradeable") is True):
                raise ValueError("Unusable quote")
            if quote_at > now + dt.timedelta(minutes=5):
                raise ValueError("Future quote")
            age = now - quote_at
            history = sorted({(m["source"], m["at"]): m for m in history}.values(),
                             key=lambda m: (m["at"], m["source"]))[-96:]
            if age > dt.timedelta(minutes=20):
                rec["tracking"] = {**prior, "state": "QUOTE_STALE",
                                   "checked_at": iso(now), "quote_at": iso(quote_at),
                                   "history": history}
                continue
            price = bid if plan["direction"] == "LONG" else ask
            if has_entry:
                reference, kind, anchor_at = (numeric(plan["planned_entry"]),
                                              "ORIGINAL_PLANNED_ENTRY",
                                              plan["created_at"])
            elif (positive_price(plan.get("entry_low")) is not None and
                  positive_price(plan.get("entry_high")) is not None):
                reference = round((numeric(plan["entry_low"]) +
                                   numeric(plan["entry_high"])) / 2, 8)
                kind, anchor_at = "ORIGINAL_ZONE_MIDPOINT", plan["created_at"]
            elif (positive_price(prior.get("reference_price")) is not None and
                  prior.get("reference_kind") == "FIRST_OBSERVED_QUOTE"):
                reference = numeric(prior["reference_price"])
                kind, anchor_at = "FIRST_OBSERVED_QUOTE", prior["reference_at"]
            else:
                reference, kind, anchor_at = price, "FIRST_OBSERVED_QUOTE", iso(quote_at)
            mark = {"at": iso(quote_at), "price": price,
                    "move_pct": research_move_pct(plan["direction"], reference, price),
                    "source": "OANDA_LIVE_QUOTE"}
            history.append(mark)
            history = sorted({(m["source"], m["at"]): m for m in history}.values(),
                             key=lambda m: (m["at"], m["source"]))[-96:]
            if result.get("scored") is True:
                state = "PAPER_CLOSED"
            elif stage in ("UNTRIGGERED", "INVALIDATED_PRE_ENTRY",
                           "AMBIGUOUS_ENTRY_BAR", "AMBIGUOUS_EXIT_BAR"):
                state = "HISTORICAL_NO_VERIFIED_ENTRY"
            elif not has_entry or stage == "UNTESTABLE":
                state = "RESEARCH_ONLY"
            elif (plan.get("expires_at") and
                  now > stamp(plan["expires_at"]) and stage != "OPEN"):
                state = "EXPIRED_NO_PAPER_ENTRY"
            else:
                state = "PAPER_OPEN" if stage == "OPEN" else "NO_VERIFIED_ENTRY"
            rec["tracking"] = {
                "state": state, "checked_at": iso(now), "quote_at": iso(quote_at),
                "bid": bid, "ask": ask, "price": price,
                "move_pct": mark["move_pct"],
                "reference_price": reference, "reference_kind": kind,
                "reference_at": anchor_at,
                "tp1_move_pct": (directional_pct(plan, plan["tp1"])
                                 if has_entry and plan.get("tp1") is not None else None),
                "stop_move_pct": (directional_pct(plan, plan["stop"])
                                  if has_entry and plan.get("stop") is not None else None),
                "history": history,
                "source": "OANDA_RESEARCH_QUOTE_NOT_PEPPERSTONE",
            }
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
            history = sorted({(m["source"], m["at"]): m for m in history}.values(),
                             key=lambda m: (m["at"], m["source"]))[-96:]
            rec["tracking"] = {**prior, "state": "QUOTE_UNAVAILABLE",
                               "checked_at": iso(now), "history": history}


def _mark(plan, bar):
    """Intrabar adverse/favourable excursions on executable exit side."""
    entry, stop = numeric(plan["planned_entry"]), numeric(plan["stop"])
    q = bar["bid"] if plan["direction"] == "LONG" else bar["ask"]
    den = abs(entry - stop)
    if plan["direction"] == "LONG":
        return (q["h"] - entry) / den, (q["l"] - entry) / den
    return (entry - q["l"]) / den, (entry - q["h"]) / den


def evidence(bar):
    """Reproducible private bid/ask bars; omitted from public dashboard exports."""
    return {"open_at": iso(bar["t"]), "bid": dict(bar["bid"]),
            "ask": dict(bar["ask"])}


def replay(plan, bars, now, observation_times=None):
    """Deterministic from first complete M15 bar; no look-ahead or invented fills."""
    err = validate(plan)
    if err:
        return {"status": "UNTESTABLE", "reason": err, "scored": False}
    created, expiry = stamp(plan["created_at"]), stamp(plan["expires_at"])
    valid = [b for b in bars if b["t"] >= created
             and b["t"] + dt.timedelta(minutes=15) <= now]
    # We cannot call an idea untriggered if we have lost the first eligible bars.
    if not valid or valid[0]["t"] > created + dt.timedelta(minutes=15):
        return {"status": "DATA_GAP", "reason": "Initial post-signal M15 candles missing", "scored": False}
    stage, result = "WAIT_TRIGGER", {"status": "PENDING_TRIGGER", "scored": False}
    triggered = None
    entered = None
    mfe = 0.0
    mae = 0.0
    holding_bars = 0
    previous = None
    # Previous close must have completed before the original plan; it cannot
    # be a post-registration candle observed with hindsight.
    previous_close = next(
        (b["mid"]["c"] for b in reversed(bars)
         if b["t"] + dt.timedelta(minutes=15) <= created), None)
    breakout_pending = None
    available = None
    for bar in valid:
        close_at = bar["t"] + dt.timedelta(minutes=15)
        if previous and bar["t"] - previous > dt.timedelta(minutes=15, seconds=1):
            # Only long weekend closure is expected; shorter missing live-session
            # intervals must not quietly turn into "untriggered" or a winner.
            weekend_closure = (bar["t"]-previous >= dt.timedelta(hours=24)
                               and previous.weekday() in (4, 5, 6))
            if not weekend_closure:
                return {"status": "DATA_GAP", "reason": "Missing live-session M15 history",
                        "scored": False}
        previous = bar["t"]
        if stage == "WAIT_TRIGGER":
            if close_at > expiry:
                break
            if _stop_hit(plan, bar):
                return {"status": "INVALIDATED_PRE_ENTRY", "reason": "Stop touched before confirmation",
                        "scored": False, "at": iso(close_at)}
            if blackout(plan, close_at, bar["t"]):
                breakout_pending = None
                previous_close = bar["mid"]["c"]
                continue
            route = "B" if _eligible_trigger(plan, bar) else None
            if plan.get("alert_a") is not None:
                a = numeric(plan["alert_a"])
                tolerance = 2 * pip_size(plan)
                long = plan["direction"] == "LONG"
                if breakout_pending is not None:
                    age = close_at - breakout_pending
                    tested = (bar["mid"]["l"] <= a + tolerance and bar["mid"]["c"] > a
                              if long else
                              bar["mid"]["h"] >= a - tolerance and bar["mid"]["c"] < a)
                    if dt.timedelta(0) < age <= dt.timedelta(minutes=60) and tested:
                        route = "A"  # Original watcher evaluates A before B.
                    if age > dt.timedelta(minutes=60) or (
                            bar["mid"]["c"] < a - tolerance if long
                            else bar["mid"]["c"] > a + tolerance):
                        breakout_pending = None
                crossed = (previous_close is not None and
                           (previous_close <= a < bar["mid"]["c"] if long
                            else previous_close >= a > bar["mid"]["c"]))
                if crossed:
                    breakout_pending = close_at
            if route is None and _eligible_continuation(plan, bar, previous_close):
                route = "C"
            previous_close = bar["mid"]["c"]
            if route:
                triggered = close_at
                if observation_times is not None:
                    detected = next((t for t in sorted(observation_times) if t >= close_at), None)
                    if detected is None or detected >= expiry:
                        continue
                    # One minute manual response; use the NEXT complete-bar open.
                    earliest = detected + dt.timedelta(minutes=1)
                    available = earliest.replace(minute=earliest.minute//15*15, second=0, microsecond=0)
                    if available < earliest:
                        available += dt.timedelta(minutes=15)
                stage = "WAIT_LIMIT"
                result = {"status": "TRIGGERED_UNFILLED", "triggered_at": iso(triggered),
                          "route": route,
                          "detected_at": iso(detected) if observation_times is not None else iso(triggered),
                          "entry_available_at": iso(available) if available else iso(triggered),
                          "scored": False, "evidence": {"confirmation": evidence(bar)}}
            continue
        if stage == "WAIT_LIMIT":
            if available and bar["t"] < available:
                if _stop_hit(plan, bar) or _tp_hit(plan, bar):
                    return {**result, "status": "INVALIDATED_PRE_ENTRY", "reason": "Exit level reached before observed alert could be acted on", "scored": False}
                continue
            if close_at > expiry:
                break
            if blackout(plan, close_at, bar["t"]):
                continue
            if result.get("route") == "C":
                # Prospective continuation enters only at the opening quote
                # of the next completed M15 bar, never inside the trigger bar.
                if bar["t"] != (available or triggered):
                    return {**result, "status": "DATA_GAP",
                            "reason": "Next completed M15 open unavailable",
                            "scored": False}
                spread_at_open = (bar["ask"]["o"]-bar["bid"]["o"]) / pip_size(plan)
                fill = (bar["ask"]["o"] if plan["direction"] == "LONG"
                        else bar["bid"]["o"])
                entry = numeric(plan["planned_entry"])
                stop, tp = numeric(plan["stop"]), numeric(plan["tp1"])
                long = plan["direction"] == "LONG"
                level = numeric(plan["alert_c"])
                atr = numeric((plan.get("technical_context") or {}).get("m15_atr"))
                if (spread_at_open > numeric(plan["max_oanda_spread_pips"])
                        or atr is None or abs(fill-entry) > .4*atr
                        or not ((fill > level and stop < fill < tp) if long
                                else (fill < level and tp < fill < stop))):
                    return {**result, "status": "INVALIDATED_PRE_ENTRY",
                            "reason": "Next-bar executable side, cost or slippage outside frozen plan",
                            "scored": False}
                risk = abs(fill-stop)
                reward = abs(tp-fill)
                spread_cost = spread_at_open * pip_size(plan)
                if risk <= 0 or reward / risk < 1.5 or (
                        reward-spread_cost)/(risk+spread_cost) < 1.35:
                    return {**result, "status": "INVALIDATED_PRE_ENTRY",
                            "reason": "Next-bar open failed original gross/net reward gates",
                            "scored": False}
                entered = bar["t"]
                result = {**result, "status": "OPEN", "entry_at": iso(entered),
                          "entry": fill, "route": "C", "scored": False,
                          "evidence": {**result.get("evidence", {}),
                                       "entry": evidence(bar)}}
                # An M15 bar that touches either exit cannot establish
                # unambiguous intrabar execution order at the opening quote.
                if _stop_hit(plan, bar) or _tp_hit(plan, bar):
                    return {**result, "status": "AMBIGUOUS_ENTRY_BAR",
                            "reason": "Continuation entry bar touched stop or target",
                            "scored": False}
                stage = "OPEN"
                continue
            close_spread = (bar["ask"]["c"] - bar["bid"]["c"]) / pip_size(plan)
            if close_spread > numeric(plan["max_oanda_spread_pips"]):
                continue
            if _stop_hit(plan, bar):
                return {**result, "status": "INVALIDATED_PRE_ENTRY",
                        "reason": "Stop reached before verified limit fill", "at": iso(close_at)}
            if not _limit_hit(plan, bar):
                continue
            entered = bar["t"]
            # Order of intra-M15 extremes is unknowable; do not claim a win.
            if _tp_hit(plan, bar):
                return {**result, "status": "AMBIGUOUS_ENTRY_BAR", "entry_at": iso(entered),
                        "reason": "Entry and target inside the same OHLC bar",
                        "evidence": {**result.get("evidence", {}), "entry": evidence(bar)},
                        "scored": False}
            stage = "OPEN"
            result = {**result, "status": "OPEN", "triggered_at": iso(triggered), "entry_at": iso(entered),
                      "entry": numeric(plan["planned_entry"]), "scored": False,
                      "evidence": {**result.get("evidence", {}), "entry": evidence(bar)}}
            continue
        if stage == "OPEN":
            holding_bars += 1
            evaluation = ({**plan, "planned_entry": result["entry"]}
                          if result.get("route") == "C" else plan)
            q = bar["bid"] if plan["direction"] == "LONG" else bar["ask"]
            adverse_open = (q["o"] <= numeric(plan["stop"]) if plan["direction"] == "LONG"
                            else q["o"] >= numeric(plan["stop"]))
            if adverse_open:
                return {**result, "status":"LOSS_STOP", "at":iso(bar["t"]),
                        "exit":q["o"], "net_r":_r(evaluation,q["o"]),
                        "evidence":{**result.get("evidence",{}),"exit":evidence(bar)},
                        "stop_gap_adjusted":True, "scored":True}
            fav, adverse = _mark(evaluation, bar)
            mfe, mae = max(mfe, fav), min(mae, adverse)
            stop_hit, tp_hit = _stop_hit(plan, bar), _tp_hit(plan, bar)
            if stop_hit and tp_hit:
                return {**result, "status": "AMBIGUOUS_EXIT_BAR", "at": iso(close_at),
                        "mfe_r": round(mfe, 3), "mae_r": round(mae, 3),
                        "evidence": {**result.get("evidence", {}), "exit": evidence(bar)},
                        "reason": "Stop and target both touched one M15 bar", "scored": False}
            if stop_hit or tp_hit:
                return {**result, "status": "LOSS_STOP" if stop_hit else "WIN_TP1",
                        "at": iso(close_at), "exit": numeric(plan["stop" if stop_hit else "tp1"]),
                        "net_r": -1.0 if stop_hit else _r(evaluation, numeric(plan["tp1"])),
                        "evidence": {**result.get("evidence", {}), "exit": evidence(bar)},
                        "mfe_r": round(mfe, 3), "mae_r": round(mae, 3), "scored": True}
            if holding_bars >= MAX_HOLD_BARS:
                q = bar["bid"] if plan["direction"] == "LONG" else bar["ask"]
                return {**result, "status": "TIME_EXIT", "at": iso(close_at), "exit": q["c"],
                        "evidence": {**result.get("evidence", {}), "exit": evidence(bar)},
                        "net_r": _r(evaluation, q["c"]), "mfe_r": round(mfe, 3),
                        "mae_r": round(mae, 3), "scored": True}
    if stage == "OPEN":
        return {**result, "mfe_r": round(mfe, 3), "mae_r": round(mae, 3)}
    if now > expiry:
        return {**result, "status": "UNTRIGGERED" if not triggered else "TRIGGERED_UNFILLED",
                "reason": "Expiry without verified entry", "scored": False}
    return result


def public_view(book):
    candidates = list(book.get("candidates", {}).values())
    qualified = [c for c in candidates if c.get("result", {}).get("scored") is True]
    wins = sum(c["result"]["net_r"] > 0 for c in qualified)
    losses = sum(c["result"]["net_r"] < 0 for c in qualified)
    strategy = {}
    for c in qualified:
        key = strategy_name(c["plan"]) + " / ROUTE_" + str(c["result"].get("route", "UNKNOWN"))
        rec = strategy.setdefault(key, {"count": 0, "wins": 0, "total_r": 0.0,
                                         "samples": [], "days": set(), "pairs": set()})
        rec["count"] += 1
        rec["wins"] += int(c["result"]["net_r"] > 0)
        rec["total_r"] += c["result"]["net_r"]
        rec["samples"].append(c["result"]["net_r"])
        rec["days"].add(str(c["plan"].get("created_at", ""))[:10])
        rec["pairs"].add(c["plan"].get("instrument", ""))
    learned = {}
    for name, row in strategy.items():
        count = row["count"]
        mean = row["total_r"] / count
        variance = sum((v-mean)**2 for v in row["samples"]) / (count-1) if count > 1 else 0
        bound = 2.58 * math.sqrt(variance / count) if count > 1 else float("inf")
        diverse = count >= 30 and len(row["days"]) >= 10 and len(row["pairs"]) >= 3
        # Bounded research-only ranking suggestion; NEVER relax live entry risk gates.
        bias = (1 if mean-bound > 0 else -1 if mean+bound < 0 else 0) if diverse else 0
        learned[name] = {"count": count, "wins": row["wins"],
                         "total_r": round(row["total_r"], 3),
                         "mean_r": round(mean, 3),
                         "distinct_days": len(row["days"]),
                         "distinct_pairs": len(row["pairs"]),
                         "lower_99pct_approx_r": round(mean-bound, 3) if math.isfinite(bound) else None,
                         "paper_priority_bias": bias,
                         "eligible_for_reinforcement": bias == 1}
    return {"schema": 1, "updated_at": book.get("updated_at"), "data_basis":
            "OANDA M15 bid/ask candle PAPER replay; not Pepperstone fills",
            "cost_policy": "OANDA bid/ask spread is reflected; Pepperstone slippage, "
                           "overnight financing, commission and taxes are not verified "
                           "or deducted. Percentage is currency price movement, not account return.",
            "method": "Confirm M15 rejection or breakout+retest, require later planned-price limit touch; "
                      "first TP1/stop or 96 M15 trading-bar time exit; ambiguous bars not scored.",
            "totals": {"candidates": len(candidates), "scored": len(qualified),
                       "wins": wins, "losses": losses},
            "strategy": {name: {key: value for key, value in row.items()
                                if key in ("count", "wins", "distinct_days",
                                           "distinct_pairs", "paper_priority_bias",
                                           "eligible_for_reinforcement")}
                         for name, row in learned.items()},
            "learning_policy": "Advisory paper research bias remains neutral until at "
                               "least 30 distinct scored signals across 10 days/3 pairs "
                               "and a conservative outcome confidence check is satisfied. "
                               "Never changes live entry risk gates or guarantees profit.",
            "candidates": [
                {"id": p["id"], "instrument": p["instrument"], "direction": p["direction"],
                 "created_at": p["created_at"], "expires_at": p.get("expires_at"),
                 "entry": p.get("planned_entry"), "entry_low": p.get("entry_low"),
                 "entry_high": p.get("entry_high"), "stop": p.get("stop"), "tp1": p.get("tp1"),
                 "tp2": p.get("tp2"), "source": p.get("source", "LIVE_SIGNAL"),
                 "strategy": strategy_name(p),
                 "rationale": str(p.get("original_note", ""))[:170],
                 "technical_context": p.get("technical_context"),
                 "tracking": ({**{k: v for k, v in (c.get("tracking") or {}).items()
                                 if k != "history"},
                               "history": ((c.get("tracking") or {}).get("history", [])[-10:])}
                              if c.get("tracking") else None),
                 "move_pct": (directional_pct(p, c["result"]["exit"])
                              if c.get("result", {}).get("scored") is True
                              and c.get("result", {}).get("exit") is not None
                              else ((c.get("tracking") or {}).get("move_pct")
                                    if (c.get("tracking") or {}).get("state") in
                                       ("PAPER_OPEN", "NO_VERIFIED_ENTRY", "RESEARCH_ONLY",
                                        "PAPER_CLOSED", "HISTORICAL_NO_VERIFIED_ENTRY",
                                        "EXPIRED_NO_PAPER_ENTRY") else None)),
                 **{k: v for k, v in c.get("result", {}).items()
                    if k in ("status", "reason", "scored", "route", "triggered_at", "entry_at",
                             "at", "entry", "exit")}}
                for c in candidates for p in [c["plan"]]]}


def strategy_name(plan):
    if plan.get("strategy"):
        return plan["strategy"]
    if plan.get("alert_a") is not None and plan.get("alert_b") is None:
        return "M15_BREAKOUT_RETEST"
    return "M15_REJECTION"


def ranked_priority_watches(summary, market, book, now, limit=4):
    """Freeze first-observed ranked research watches, not approved entries.

    Each distinct collector snapshot creates its own immutable observation,
    even for the same pair/direction. Later snapshots never rewrite its
    first reference quote or turn an untriggered watch into a filled trade.
    """
    from zoneinfo import ZoneInfo

    snapshot = summary.get("collected_at")
    if not snapshot or snapshot != (market or {}).get("collected_at"):
        return []
    try:
        at = stamp(snapshot)
    except (TypeError, ValueError, OverflowError):
        return []
    if at > now + dt.timedelta(minutes=5) or now - at > dt.timedelta(minutes=20):
        return []
    london_local = at.astimezone(ZoneInfo("Europe/London"))
    london_day = london_local.strftime("%Y%m%d")
    # UTC snapshot identity distinguishes the repeated 01:15 hour at the
    # October DST transition, and main research snapshots from hourly ones.
    snapshot_key = at.strftime("%Y%m%dT%H%M%SZ")
    assessment_kind = ("MAIN_RESEARCH_WINDOW" if
                       (london_local.hour, london_local.minute // 15 * 15) in
                       ((7, 30), (12, 0), (14, 0), (16, 30)) else
                       "HOURLY_RESEARCH")
    watched = []
    already = (book or {}).get("candidates") or {}
    # Preserve the strongest three every pass, then favour newly observed
    # pair/direction hypotheses from the top twelve. This is diversity in
    # PAPER research coverage, never a relaxation of live entry quality gates.
    ranked = (summary.get("ranked_review_candidates") or [])[:12]
    seen_today = {(row.get("plan") or {}).get("instrument", "") + ":" +
                  (row.get("plan") or {}).get("direction", "")
                  for row in already.values()
                  if str((row.get("plan") or {}).get("created_at", ""))[:10] ==
                     at.strftime("%Y-%m-%d")}
    top = ranked[:min(3, limit)]
    remaining = ranked[len(top):]
    remaining = sorted(remaining, key=lambda item:
        (str(item.get("instrument", "")) + ":" +
         str(item.get("potential_direction_not_approved", ""))) in seen_today)
    selected = top + remaining[:max(0, limit-len(top))]
    for item in selected:
        if not isinstance(item, dict):
            continue
        instrument = item.get("instrument")
        direction = item.get("potential_direction_not_approved")
        if (not isinstance(instrument, str) or len(instrument) != 7
                or instrument[3] != "_" or not instrument.replace("_", "").isalpha()
                or instrument == "GBP_ZAR" or direction not in ("LONG", "SHORT")):
            continue
        quote = (((market or {}).get("pairs") or {}).get(instrument) or {}).get("quote") or {}
        try:
            qt = stamp(quote["time"])
            bid, ask = positive_price(quote.get("bid")), positive_price(quote.get("ask"))
            if (quote.get("tradeable") is not True or bid is None or ask is None
                    or bid > ask or qt > now + dt.timedelta(minutes=5)
                    or now - qt > dt.timedelta(minutes=20)):
                continue
        except (ValueError, KeyError, TypeError, OverflowError):
            continue
        key = "PRIORITY-" + snapshot_key + "-" + instrument + "-" + direction
        if key in already:
            continue
        watched.append({
            "id": key, "instrument": instrument, "direction": direction,
            "created_at": iso(at), "source": "AUTOMATED_RESEARCH_PRIORITY",
            "source_ref": snapshot_key,
            "strategy": "RESEARCH_PRIORITY_WATCH",
            "original_note": "Top ranked OANDA technical research at first observation. Not an entry recommendation; no broker execution or paper position is implied.",
            "technical_context": {
                "first_research_priority": item.get("research_priority"),
                "first_ranked_at": iso(at),
                "assessment_kind": assessment_kind,
                "assessment_day_uk": london_day,
                "research_priority_rank": (summary.get("ranked_review_candidates") or []).index(item) + 1,
            },
        })
    return watched


def update_book(book, seeds, latest, now, fetch=None, market=None):
    rows = book.setdefault("candidates", {})
    messages = book.setdefault("warnings", [])
    for raw in (seeds.get("candidates") or []) + (latest.get("signals") or []):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        plan = frozen(raw)
        if plan["id"] in rows:
            if economic_terms(rows[plan["id"]]["plan"]) != economic_terms(plan):
                msg = "IMMUTABLE_PLAN_CONFLICT:" + plan["id"]
                if msg not in messages:
                    messages.append(msg)
            else:
                previous_plan = rows[plan["id"]]["plan"]
                # Repair missing historical *descriptive* zones only when the
                # immutable original source and commentary match exactly.
                # Never backfill missing fill/stop/target economics or exits.
                if (previous_plan.get("source") == plan.get("source")
                        and previous_plan.get("original_note") == plan.get("original_note")
                        and previous_plan.get("created_at") == plan.get("created_at")):
                    repaired = []
                    for key in ("entry_low", "entry_high"):
                        if key not in previous_plan and key in plan:
                            previous_plan[key] = plan[key]
                            repaired.append(key)
                    if repaired:
                        rows[plan["id"]]["events"].append({
                            "at": iso(now), "to": "ORIGINAL_SOURCE_ZONE_METADATA_RESTORED",
                            "fields": repaired})
            continue
        if len(rows) >= MAX_CANDIDATES:
            msg = "CANDIDATE_LIMIT_REACHED"
            if msg not in messages:
                messages.append(msg)
            break
        rows[plan["id"]] = {"plan": plan, "first_recorded_at": iso(now),
                            "result": {"status": "NOT_EVALUATED", "scored": False},
                            "events": []}
    due = sorted(rows.values(), key=lambda c: c["plan"].get("created_at", ""))
    used = 0
    candle_samples = {}
    for rec in due:
        plan = rec["plan"]
        issue = validate(plan)
        old = rec.get("result", {})
        terminal = ("LOSS_STOP", "WIN_TP1", "TIME_EXIT", "AMBIGUOUS_EXIT_BAR",
                    "AMBIGUOUS_ENTRY_BAR", "INVALIDATED_PRE_ENTRY", "UNTRIGGERED")
        if (old.get("status") in ("WIN_TP1", "LOSS_STOP", "TIME_EXIT",
                                  "AMBIGUOUS_EXIT_BAR", "AMBIGUOUS_ENTRY_BAR")
                and not old.get("route") and
                plan.get("alert_b") is not None and plan.get("alert_a") is None):
            old["route"] = "B"
            rec["events"].append({"at": iso(now), "to": "ENTRY_ROUTE_FROM_ORIGINAL_PLAN"})
        if issue:
            result = {"status": "UNTESTABLE", "reason": issue, "scored": False}
        elif old.get("status") in terminal and old.get("evidence"):
            # Terminal records are immutable: market history cannot rewrite them.
            continue
        elif old.get("status") in ("UNTRIGGERED", "INVALIDATED_PRE_ENTRY"):
            continue
        elif used >= MAX_REMOTE_CALLS:
            continue
        else:
            used += 1
            try:
                bars = source_bars(plan, now, fetch)
                candle_samples[plan["id"]] = bars
                result = replay(plan, bars, now)
            except (OSError, RuntimeError, ValueError, TimeoutError, KeyError, TypeError) as exc:
                result = {"status": "DATA_UNAVAILABLE", "reason": type(exc).__name__,
                          "scored": False}
        if old.get("status") in terminal:
            if (result.get("status") == old.get("status") and
                    result.get("net_r") == old.get("net_r")):
                if result.get("evidence"):
                    old["evidence"] = result["evidence"]
                    rec["events"].append({"at": iso(now), "to": "EVIDENCE_BACKFILLED"})
            elif result.get("status") not in ("DATA_UNAVAILABLE", "DATA_GAP"):
                msg = "TERMINAL_REPLAY_MISMATCH:" + plan["id"]
                if msg not in messages:
                    messages.append(msg)
            continue
        try:
            from scripts.paper_evidence_guard import reconcile
        except ModuleNotFoundError:
            from paper_evidence_guard import reconcile
        result = reconcile(rec, result, iso(now))
        if old.get("status") != result["status"]:
            rec["events"].append({"at": iso(now), "from": old.get("status"),
                                  "to": result["status"]})
        rec["result"] = result
        rec["last_checked_at"] = iso(now)
    if market is not None:
        update_price_tracking(book, market, now, candle_samples)
    book["schema"] = 1
    book["updated_at"] = iso(now)
    book["simulator_version"] = "paper-m15-a-b-limit-v3"
    return book


def main():
    now = utc_now()
    book = load(OUT, {"candidates": {}, "warnings": []})
    market = load(Path("data/latest.json"), {"pairs": {}})
    summary = load(Path("data/summary.json"), {})
    seeds = load(PLANS, {"candidates": []})
    # Separate research watches from formal entry plans; never count as wins.
    seeds["candidates"] = list(seeds.get("candidates") or []) + ranked_priority_watches(
        summary, market, book, now, limit=6)
    update_book(book, seeds, load(LIVE, {"signals": []}), now, market=market)
    save(OUT, book)
    view = public_view(book)
    print("FX PAPER AUDIT", view["totals"], "warnings", book.get("warnings", []))


if __name__ == "__main__":
    main()


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
MAX_CANDIDATES = 200
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


def validate(plan):
    required = ("id", "instrument", "direction", "created_at", "expires_at",
                "planned_entry", "stop", "tp1", "alert_b", "max_oanda_spread_pips")
    if any(plan.get(k) is None for k in required):
        return "ORIGINAL_LEVELS_OR_TRIGGER_MISSING"
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
        numeric(plan["alert_b"])
    except (TypeError, ValueError, OverflowError, KeyError):
        return "INVALID_PLAN"
    return None


def frozen(plan):
    """No revisions of prices/time/trigger under the same ID."""
    fields = ("id", "instrument", "direction", "created_at", "expires_at",
              "planned_entry", "stop", "tp1", "tp2", "alert_b",
              "max_oanda_spread_pips", "event_blackouts_utc", "source",
              "source_ref", "original_note", "strategy", "technical_context")
    return {k: plan[k] for k in fields if k in plan}


def pip_size(plan):
    return 0.01 if plan["instrument"].endswith("_JPY") else 0.0001


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
        return parse_bars(json.load(response))


def _eligible_trigger(plan, bar):
    """Completed M15 rejection; a wick or a guessed M5 confirmation is insufficient."""
    mid = bar["mid"]
    threshold = numeric(plan["alert_b"])
    entry = numeric(plan["planned_entry"])
    long = plan["direction"] == "LONG"
    touched = mid["l"] <= threshold if long else mid["h"] >= threshold
    direction = (mid["c"] > mid["o"] and mid["c"] >= entry) if long else (
        mid["c"] < mid["o"] and mid["c"] <= entry)
    spread = (bar["ask"]["c"] - bar["bid"]["c"]) / pip_size(plan)
    return touched and direction and spread <= numeric(plan["max_oanda_spread_pips"])


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


def replay(plan, bars, now):
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
    for bar in valid:
        close_at = bar["t"] + dt.timedelta(minutes=15)
        if previous and bar["t"] - previous > dt.timedelta(minutes=30):
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
                continue
            if _eligible_trigger(plan, bar):
                triggered = close_at
                stage = "WAIT_LIMIT"
                result = {"status": "TRIGGERED_UNFILLED", "triggered_at": iso(triggered),
                          "scored": False, "evidence": {"confirmation": evidence(bar)}}
            continue
        if stage == "WAIT_LIMIT":
            if close_at > expiry:
                break
            if blackout(plan, close_at, bar["t"]):
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
            result = {"status": "OPEN", "triggered_at": iso(triggered), "entry_at": iso(entered),
                      "entry": numeric(plan["planned_entry"]), "scored": False,
                      "evidence": {**result.get("evidence", {}), "entry": evidence(bar)}}
            continue
        if stage == "OPEN":
            holding_bars += 1
            fav, adverse = _mark(plan, bar)
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
                        "net_r": -1.0 if stop_hit else _r(plan, numeric(plan["tp1"])),
                        "evidence": {**result.get("evidence", {}), "exit": evidence(bar)},
                        "mfe_r": round(mfe, 3), "mae_r": round(mae, 3), "scored": True}
            if holding_bars >= MAX_HOLD_BARS:
                q = bar["bid"] if plan["direction"] == "LONG" else bar["ask"]
                return {**result, "status": "TIME_EXIT", "at": iso(close_at), "exit": q["c"],
                        "evidence": {**result.get("evidence", {}), "exit": evidence(bar)},
                        "net_r": _r(plan, q["c"]), "mfe_r": round(mfe, 3),
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
        key = c["plan"].get("strategy", "M15_REJECTION")
        rec = strategy.setdefault(key, {"count": 0, "wins": 0, "total_r": 0.0})
        rec["count"] += 1
        rec["wins"] += int(c["result"]["net_r"] > 0)
        rec["total_r"] += c["result"]["net_r"]
    return {"schema": 1, "updated_at": book.get("updated_at"), "data_basis":
            "OANDA M15 bid/ask candle PAPER replay; not Pepperstone fills",
            "method": "Confirm M15 rejection, require later planned-price limit touch; "
                      "first TP1/stop or 96 M15 trading-bar time exit; ambiguous bars not scored.",
            "totals": {"candidates": len(candidates), "scored": len(qualified),
                       "wins": wins, "losses": losses,
                       "net_r": round(sum(c["result"]["net_r"] for c in qualified), 3)},
            "strategy": {k: {**v, "total_r": round(v["total_r"], 3),
                            "eligible_for_reinforcement": v["count"] >= 30 and
                            v["total_r"] > 0} for k, v in strategy.items()},
            "candidates": [
                {"id": p["id"], "instrument": p["instrument"], "direction": p["direction"],
                 "created_at": p["created_at"], "expires_at": p.get("expires_at"),
                 "entry": p.get("planned_entry"), "stop": p.get("stop"), "tp1": p.get("tp1"),
                 "tp2": p.get("tp2"), "source": p.get("source", "LIVE_SIGNAL"),
                 "strategy": p.get("strategy", "M15_REJECTION"),
                 "rationale": str(p.get("original_note", ""))[:170],
                 "technical_context": p.get("technical_context"),
                 **{k: v for k, v in c.get("result", {}).items()
                    if k in ("status", "reason", "scored", "triggered_at", "entry_at",
                             "at", "entry", "exit", "net_r", "mfe_r", "mae_r")}}
                for c in candidates[-90:] for p in [c["plan"]]]}


def update_book(book, seeds, latest, now, fetch=None):
    rows = book.setdefault("candidates", {})
    messages = book.setdefault("warnings", [])
    for raw in (seeds.get("candidates") or []) + (latest.get("signals") or []):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        plan = frozen(raw)
        if plan["id"] in rows:
            if rows[plan["id"]]["plan"] != plan:
                msg = "IMMUTABLE_PLAN_CONFLICT:" + plan["id"]
                if msg not in messages:
                    messages.append(msg)
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
    for rec in due:
        plan = rec["plan"]
        issue = validate(plan)
        old = rec.get("result", {})
        terminal = ("LOSS_STOP", "WIN_TP1", "TIME_EXIT", "AMBIGUOUS_EXIT_BAR",
                    "AMBIGUOUS_ENTRY_BAR", "INVALIDATED_PRE_ENTRY", "UNTRIGGERED")
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
                result = replay(plan, source_bars(plan, now, fetch), now)
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
        if old.get("status") != result["status"]:
            rec["events"].append({"at": iso(now), "from": old.get("status"),
                                  "to": result["status"]})
        rec["result"] = result
        rec["last_checked_at"] = iso(now)
    book["schema"] = 1
    book["updated_at"] = iso(now)
    book["simulator_version"] = "paper-rejection-limit-v2"
    return book


def main():
    now = utc_now()
    book = load(OUT, {"candidates": {}, "warnings": []})
    update_book(book, load(PLANS, {"candidates": []}),
                load(LIVE, {"signals": []}), now)
    save(OUT, book)
    view = public_view(book)
    print("FX PAPER AUDIT", view["totals"], "warnings", book.get("warnings", []))


if __name__ == "__main__":
    main()

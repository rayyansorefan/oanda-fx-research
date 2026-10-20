#!/usr/bin/env python3
"""Watch 1–3 explicitly approved FX setups. Research alerts only; NEVER submit orders."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SNAPSHOT = Path("data/latest.json")
SIGNALS = Path("signals.json")
STATE = Path("data/watch_state.json")


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc_now():
    return datetime.now(timezone.utc)


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def blacked_out(signal, current):
    for window in signal.get("event_blackouts_utc", []):
        if parse_time(window["start"]) <= current <= parse_time(window["end"]):
            return True
    return False


def valid_risk(signal, observed_spread):
    entry, stop, target = (float(signal[k]) for k in ("planned_entry", "stop", "tp1"))
    long = signal["direction"] == "LONG"
    if not ((stop < entry < target) if long else (target < entry < stop)):
        return False
    pip = .01 if signal["instrument"].endswith("_JPY") else .0001
    spread = observed_spread * pip
    gross = abs(target - entry) / abs(entry - stop)
    estimated = (abs(target - entry) - spread) / (abs(entry - stop) + spread)
    return gross >= 1.5 and estimated >= 1.35


def signal_match(signal, pairdata, state_for_signal, current):
    """Examine all newly closed M15 candles since the last hourly run."""
    quote = pairdata["quote"]
    if not quote["tradeable"] or quote["spread_pips"] < 0:
        return None
    if quote["spread_pips"] > float(signal.get("max_oanda_spread_pips", 2.0)):
        return None
    if (current - parse_time(quote["time"])).total_seconds() > 300:
        return None
    if not valid_risk(signal, quote["spread_pips"]):
        return None
    # A historical M15 trigger is only a reason to reassess if the latest
    # market remains inside the planned stop/target envelope with reward
    # still available after the observed OANDA spread.
    entry = float(signal["planned_entry"])
    stop = float(signal["stop"])
    tp1 = float(signal["tp1"])
    long = signal["direction"] == "LONG"
    current_executable = quote["ask"] if long else quote["bid"]
    if long:
        if current_executable <= stop or current_executable >= tp1:
            return None
        reward = tp1 - current_executable
        risk = current_executable - stop
    else:
        if current_executable >= stop or current_executable <= tp1:
            return None
        reward = current_executable - tp1
        risk = stop - current_executable
    if risk <= 0 or reward / risk < 1.35:
        return None
    created = parse_time(signal["created_at"])
    candles = pairdata["frames"]["M15"]["recent_completed_candles"]
    if len(candles) < 2:
        return None
    last_processed = parse_time(state_for_signal.get("last_processed_bar_close", signal["created_at"]))
    long = signal["direction"] == "LONG"
    tolerance = 0.02 if signal["instrument"].endswith("_JPY") else 0.0002
    for idx in range(1, len(candles)):
        prev, curr = candles[idx - 1], candles[idx]
        candle_start = parse_time(curr["time"])
        close_time = candle_start + timedelta(minutes=15)
        if close_time <= created or close_time <= last_processed or close_time > current:
            continue
        if current - close_time > timedelta(minutes=75):
            state_for_signal["last_processed_bar_close"] = close_time.isoformat()
            continue
        if blacked_out(signal, close_time):
            state_for_signal["last_processed_bar_close"] = close_time.isoformat()
            continue
        close = curr["mid"]["c"]
        previous_close = prev["mid"]["c"]
        a = signal.get("alert_a")
        if a is not None and "A" not in state_for_signal.get("notified", []):
            level = float(a)
            pending = state_for_signal.get("breakout_pending")
            if pending:
                age = close_time - parse_time(pending)
                tested = (curr["mid"]["l"] <= level + tolerance and close > level) if long else (
                    curr["mid"]["h"] >= level - tolerance and close < level)
                if timedelta(minutes=0) < age <= timedelta(minutes=60) and tested:
                    state_for_signal["last_processed_bar_close"] = close_time.isoformat()
                    return "A", curr["time"], "Completed breakout and later M15 retest"
                failed = close < level - tolerance if long else close > level + tolerance
                if age > timedelta(minutes=60) or failed:
                    state_for_signal.pop("breakout_pending", None)
            crossed = (previous_close <= level < close) if long else (
                previous_close >= level > close)
            if crossed:
                state_for_signal["breakout_pending"] = close_time.isoformat()
        b = signal.get("alert_b")
        if b is not None and "B" not in state_for_signal.get("notified", []):
            level = float(b)
            rejection = (curr["mid"]["l"] <= level and close > level and
                         close > curr["mid"]["o"]) if long else (
                         curr["mid"]["h"] >= level and close < level and
                         close < curr["mid"]["o"])
            if rejection:
                state_for_signal["last_processed_bar_close"] = close_time.isoformat()
                return "B", curr["time"], "Completed M15 rejection and reclaim"
        state_for_signal["last_processed_bar_close"] = close_time.isoformat()
    return None

def notify(signal, route, bar_time, explanation, quote):
    """Create a GitHub issue; no external messaging, no orders, no user credentials."""
    token = os.environ.get("GITHUB_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError("GitHub issue notification unavailable")
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", signal["id"])[:60]
    title = "[FX WATCH] " + signal["instrument"] + " " + signal["direction"] + " " + route + " / " + safe_id
    body = (
        "**PRICE-ONLY REASSESSMENT ALERT — NOT AN INSTRUCTION TO TRADE.**\n\n"
        "Setup: " + safe_id + " | route: " + route + "\n"
        "OANDA completed M15 candle started (UTC): " + bar_time + "\n"
        "Confirmation: " + explanation + "\n"
        "OANDA quote timestamp (UTC): " + quote["time"] + "\n"
        "OANDA bid: " + str(quote["bid"]) + ", ask: " + str(quote["ask"]) +
        ", spread (pips): " + str(round(quote["spread_pips"], 2)) + "\n\n"
        "Planning references only — NOT verified Pepperstone execution levels:\n"
        "Entry: " + str(signal["planned_entry"]) + "; stop: " + str(signal["stop"]) +
        "; TP1: " + str(signal["tp1"]) + "; TP2: " + str(signal.get("tp2", "n/a")) + "\n\n"
        "**Before acting:** independently check Pepperstone executable bid/ask and spread, "
        "current M15 candle structure, live economic news, event blackout, correlated "
        "positions and risk. OANDA quotes are not Pepperstone quotes. Signals can "
        "reverse or fail after an alert. No orders have been placed.\n"
    )
    data = json.dumps({"title": title, "body": body}).encode()
    req = Request("https://api.github.com/repos/" + repository + "/issues",
                  data=data, method="POST",
                  headers={"Authorization": "Bearer " + token,
                           "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json"})
    with urlopen(req, timeout=20) as resp:
        result = json.load(resp)
    print("Review notification:", result.get("html_url", "[URL unavailable]"))


def watch():
    snapshot = load(SNAPSHOT, {})
    if snapshot.get("status") != "OK":
        print("NO VERIFIED SIGNAL: OANDA data unavailable")
        return
    current = utc_now()
    signals = load(SIGNALS, {"signals": []}).get("signals", [])
    if len(signals) > 3:
        raise ValueError("Maximum of three monitored trade ideas at once")
    state = load(STATE, {"setups": {}})
    for signal in signals:
        identity = signal.get("id", "")
        pair = signal.get("instrument", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", identity):
            raise ValueError("Invalid signal ID")
        if pair not in snapshot["pairs"] or signal.get("direction") not in ("LONG", "SHORT"):
            raise ValueError("Invalid pair or direction in signals.json")
        if ("created_at" not in signal or current > parse_time(signal["expires_at"])
                or blacked_out(signal, current)):
            continue
        if current < parse_time(signal["created_at"]):
            continue
        tracked = state["setups"].setdefault(identity, {"notified": []})
        if tracked.get("notified"):
            continue  # Alternate routes are mutually exclusive; only one alert per idea.
        found = signal_match(signal, snapshot["pairs"][pair], tracked, current)
        if not found:
            continue
        route, bar_time, reason = found
        notify(signal, route, bar_time, reason, snapshot["pairs"][pair]["quote"])
        tracked["notified"].append(route)
        tracked["breakout_pending"] = None
    save(STATE, state)
    print("Checked", len(signals), "approved ideas; all alerts require manual verification")


if __name__ == "__main__":
    try:
        watch()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print("Watch failed:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)

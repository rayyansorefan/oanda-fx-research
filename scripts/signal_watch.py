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
NEWS = Path("data/news_latest.json")
ASSESSMENT = Path("data/hourly_assessment.json")


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


def valid_risk(signal, observed_spread, observed_pip_size=None):
    entry, stop, target = (float(signal[k]) for k in ("planned_entry", "stop", "tp1"))
    long = signal["direction"] == "LONG"
    if not ((stop < entry < target) if long else (target < entry < stop)):
        return False
    pip = (float(observed_pip_size) if observed_pip_size is not None
           else (.01 if signal["instrument"].endswith("_JPY") else .0001))
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
    if not valid_risk(signal, quote["spread_pips"], quote.get("pip_size")):
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
    tolerance = 2 * quote.get("pip_size",
            0.01 if signal["instrument"].endswith("_JPY") else 0.0001)
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
    data = json.dumps({"title": title, "body": body,
                       "assignees": [repository.split("/")[0]]}).encode()
    req = Request("https://api.github.com/repos/" + repository + "/issues",
                  data=data, method="POST",
                  headers={"Authorization": "Bearer " + token,
                           "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json"})
    with urlopen(req, timeout=20) as resp:
        result = json.load(resp)
    print("Review notification:", result.get("html_url", "[URL unavailable]"))



def news_check(signal, feed, now):
    """Return objective news/event risk. No headline sentiment/forecasting."""
    checked = parse_time(feed.get("collected_at", "1900-01-01T00:00:00Z"))
    if now - checked > timedelta(minutes=15):
        return "NEWS_COVERAGE_STALE", [], []
    pair = signal["instrument"]
    coverage = feed.get("by_pair", {}).get(pair)
    if not isinstance(coverage, dict):
        return "NEWS_COVERAGE_INCOMPLETE", [], []
    start = parse_time(signal["created_at"])
    # Capture known material news even when other providers fail.
    recent = [item for item in coverage.get("recent_relevant_headlines", [])
              if parse_time(item["published_at"]) > start
              and now - parse_time(item["published_at"]) < timedelta(hours=7)]
    events = coverage.get("near_high_impact_calendar", [])
    if events:
        return "HIGH_IMPACT_RELEASE_WINDOW", recent[:6], events
    if recent:
        return "NEW_MATERIAL_HEADLINE_REVIEW", recent[:6], []
    # The weekly event calendar is a mandatory input. Missing pair-specific
    # central bank or general market news blocks price-only alerts; optional
    # BLS-feed HTTP errors still appear in coverage, but do not freeze ALL pairs.
    if coverage.get("calendar_coverage") != "OK":
        return "EVENT_CALENDAR_UNAVAILABLE", [], []
    sources = coverage.get("coverage", {})
    if sources.get("status") == "UNAVAILABLE":
        return "NEWS_COVERAGE_INCOMPLETE", [], []
    if sources.get("status") == "LIMITED":
        return "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES_WITH_GAPS", [], []
    if sources.get("status") == "OK" and feed.get("status") == "OK":
        return "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES", [], []
    return "NEWS_COVERAGE_INCOMPLETE", [], []


def observed_price_movement(pairdata, signal, item):
    """Price delta from the last completed M15 close BEFORE headline timestamp.
    Observed association only: not a claim news CAUSED the price change.
    """
    published = parse_time(item["published_at"])
    candles = pairdata.get("frames", {}).get("M15", {}).get("recent_completed_candles", [])
    before = [c for c in candles if
              parse_time(c["time"]) + timedelta(minutes=15) <= published]
    after = [c for c in candles if parse_time(c["time"]) + timedelta(minutes=15) > published]
    if not before or not after:
        return {"status": "PRICE_REACTION_UNVERIFIABLE"}
    earlier = before[-1]["mid"]["c"]
    later = after[-1]["mid"]["c"]
    pip = pairdata.get("quote", {}).get("pip_size",
             .01 if signal["instrument"].endswith("_JPY") else .0001)
    pips = (later - earlier) / pip
    aligned = (pips > 0) if signal["direction"] == "LONG" else (pips < 0)
    return {"status": "OBSERVED_PRICE_MOVEMENT_NOT_CAUSAL",
            "from_bar_time": before[-1]["time"], "to_bar_time": after[-1]["time"],
            "change_pips": round(pips, 2),
            "vs_planned_direction": "SUPPORTS" if aligned else "OPPOSES" if pips else "FLAT"}


def news_alert(signal, status, headlines, events, report):
    """At most one GitHub risk-review issue per setup per run. No entry orders."""
    token = os.environ.get("GITHUB_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError("GitHub issue notifications unavailable")
    title = "[FX NEWS REVIEW] " + signal["instrument"] + " " + signal["direction"] + " / " + signal["id"]
    lines = ["**REASSESSMENT REQUIRED — NO NEW ENTRY AUTHORISED.**",
             "Status: " + status,
             "Original setup: " + signal["id"],
             "Hourly news and technical report: data/hourly_assessment.json",
             "OANDA prices differ from Pepperstone. News classification is heuristic.",
             "",
             "**Newly relevant developments:**"]
    for item in headlines[:5]:
        lines.append("- " + item["published_at"] + " | " + item["title"] + " | " + item["url"])
    if events:
        lines.append("**High-impact events in blackout window (third-party indicative calendar):**")
        for event in events[:5]:
            lines.append("- " + event["time"] + " | " + event["currency"] + " | " + event["title"])
    for event in report.get("observed_price_reactions", [])[:3]:
        result = event.get("observation", {})
        lines.append("Observed M15 price movement (NOT causal): " + str(result))
    lines.append("**Action:** Fresh comprehensive news evaluation and Pepperstone price/spread "
                 "verification required. This issue is not a trade recommendation.")
    request_data = json.dumps({"title": title, "body": "\n".join(lines),
                               "assignees": [repository.split("/")[0]]}).encode()
    req = Request("https://api.github.com/repos/" + repository + "/issues",
                  data=request_data, method="POST",
                  headers={"Authorization": "Bearer " + token,
                           "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json"})
    with urlopen(req, timeout=20) as response:
        result = json.load(response)
    print("NEWS REVIEW ISSUE:", result.get("html_url", "[unavailable]"))


def basic_technical_status(pairdata, signal):
    """Lightweight monitoring of original approved plan, not a new analysis."""
    m15 = pairdata.get("frames", {}).get("M15", {})
    h1 = pairdata.get("frames", {}).get("H1", {})
    m = m15.get("indicators", {})
    h = h1.get("indicators", {})
    quote = pairdata.get("quote", {})
    bid, ask = quote.get("bid"), quote.get("ask")
    if bid is None or ask is None:
        return {"status": "NO_QUOTE"}
    mark = ask if signal["direction"] == "LONG" else bid
    stop, tp = float(signal["stop"]), float(signal["tp1"])
    long = signal["direction"] == "LONG"
    distance_to_target = (tp - mark) if long else (mark - tp)
    distance_to_stop = (mark - stop) if long else (stop - mark)
    pip = quote.get("pip_size", 0.01 if signal["instrument"].endswith("_JPY") else 0.0001)
    remaining_rr = (distance_to_target / distance_to_stop) if distance_to_stop > 0 else None
    ema20 = m.get("ema20")
    macd = m.get("macd_12_26_9") or {}
    return {
        "oanda_quote": quote, "price_reference": mark,
        "indicative_distance_to_target_pips": round(distance_to_target / pip, 2),
        "indicative_distance_to_stop_pips": round(distance_to_stop / pip, 2),
        "indicative_remaining_rr_before_new_spread_adjustment": (
            round(remaining_rr, 2) if remaining_rr is not None else None),
        "quote_is_tradeable": quote.get("tradeable"),
        "m15_rsi14": m.get("rsi14"), "m15_macd_histogram": macd.get("histogram"),
        "m15_adx14": m.get("adx14"), "m15_atr14": m.get("atr14"),
        "h1_rsi14": h.get("rsi14"), "h1_adx14": h.get("adx14"),
        "vs_m15_ema20": (
            "ABOVE" if (bid + ask) / 2 > ema20 else "BELOW") if ema20 is not None else None,
        "latest_completed_m15_bar_start": m15.get("completed_at"),
        "interpretation": "Monitoring checkpoint, not full technical/macro assessment or execution instruction."
    }


def setup_already_breached(pairdata, signal, now):
    """Conservatively invalidate once plan's stop or TP1 was touched after creation.
    OHLC cannot establish same-bar ordering; no executed-trade claim.
    """
    stop, tp = float(signal["stop"]), float(signal["tp1"])
    long = signal["direction"] == "LONG"
    created = parse_time(signal["created_at"])
    bars = pairdata.get("frames", {}).get("M15", {}).get("recent_completed_candles", [])
    for candle in bars:
        start = parse_time(candle["time"])
        if start + timedelta(minutes=15) <= created or start >= now:
            continue
        # OANDA spread-side OHLC: conservative for a *potential* touch only.
        mid = candle["mid"]
        if (mid["l"] <= stop if long else mid["h"] >= stop):
            return "PLAN_STOP_LEVEL_TOUCHED_ON_M15"
        if (mid["h"] >= tp if long else mid["l"] <= tp):
            return "PLAN_TP1_LEVEL_TOUCHED_ON_M15"
    return None

def watch():
    snapshot = load(SNAPSHOT, {})
    current = utc_now()
    news = load(NEWS, {})
    signals = load(SIGNALS, {"signals": []}).get("signals", [])
    if len(signals) > 3:
        raise ValueError("Maximum three approved FX research ideas")
    state = load(STATE, {"setups": {}})
    report = {"collected_at": current.isoformat(), "status": "OK",
              "oanda_collected_at": snapshot.get("collected_at"),
              "news_collected_at": news.get("collected_at"),
              "news_coverage": news.get("status", "UNAVAILABLE"),
              "data_policy": "Research-only, no orders. Hourly OANDA quote not Pepperstone executable quote.",
              "signals": []}
    if snapshot.get("status") not in ("OK", "PARTIAL"):
        report["status"] = "OANDA_DATA_UNAVAILABLE"
        save(ASSESSMENT, report)
        return
    for signal in signals:
        identity = signal.get("id", "")
        pair = signal.get("instrument", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", identity):
            raise ValueError("Invalid signal ID")
        if pair not in snapshot.get("pairs", {}) or signal.get("direction") not in ("LONG", "SHORT"):
            raise ValueError("Unknown pair or direction")
        pairdata = snapshot["pairs"][pair]
        row = {"id": identity, "pair": pair, "direction": signal["direction"],
               "created_at": signal.get("created_at"),
               "expires_at": signal.get("expires_at"),
               "technical_checkpoint": basic_technical_status(pairdata, signal)}
        # Broader discovery may have only M15/H1, not all required frames.
        # A pair with quoted prices but absent completed M15 data is never a signal.
        if not pairdata.get("frames", {}).get("M15", {}).get("recent_completed_candles"):
            row["status"] = "PAIR_M15_CANDLES_UNAVAILABLE"
            report["signals"].append(row)
            continue
        if not signal.get("created_at") or not signal.get("expires_at"):
            row["status"] = "INVALID_SETUP"
            report["signals"].append(row)
            continue
        if current < parse_time(signal["created_at"]) or current > parse_time(signal["expires_at"]):
            row["status"] = "EXPIRED_OR_FUTURE"
            report["signals"].append(row)
            continue
        tracked = state["setups"].setdefault(identity, {"notified": []})
        breached = setup_already_breached(pairdata, signal, current)
        if breached:
            row["status"] = "REASSESS_OR_EXPIRE_PRIOR_PLAN_LEVEL_REACHED"
            row["reason"] = breached
            report["signals"].append(row)
            continue
        status, headlines, events = news_check(signal, news, current)
        row["news_status"] = status
        row["news_source_coverage"] = news.get("by_pair", {}).get(pair, {}).get("coverage", {})
        row["relevant_headlines"] = headlines
        row["release_blackouts"] = events
        row["observed_price_reactions"] = [{
            "headline": item["title"],
            "observation": observed_price_movement(pairdata, signal, item)
        } for item in headlines[:4]]
        if blacked_out(signal, current):
            row["status"] = "MANUAL_EVENT_BLACKOUT"
            report["signals"].append(row)
            continue
        if status not in ("NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES",
                          "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES_WITH_GAPS"):
            row["status"] = "REASSESS_NEWS_FIRST" if status in (
                "HIGH_IMPACT_RELEASE_WINDOW", "NEW_MATERIAL_HEADLINE_REVIEW") else (
                "NEWS_COVERAGE_NOT_VERIFIED")
            if status in ("HIGH_IMPACT_RELEASE_WINDOW", "NEW_MATERIAL_HEADLINE_REVIEW"):
                key = status + "|" + (headlines[0]["id"] if headlines else
                                     events[0]["time"] + events[0]["title"])
                if key not in tracked.get("news_alerted", []):
                    news_alert(signal, status, headlines, events, row)
                    tracked.setdefault("news_alerted", []).append(key)
            report["signals"].append(row)
            continue
        if tracked.get("notified"):
            row["status"] = "PRIOR_PRICE_ALERT_SENT"
            report["signals"].append(row)
            continue
        if status == "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES_WITH_GAPS":
            row["caution"] = "Some optional news feeds unavailable; calendar and required headline feeds checked. This is NOT an all-clear."
        found = signal_match(signal, pairdata, tracked, current)
        if found:
            route, bar_time, reason = found
            notify(signal, route, bar_time, reason, pairdata["quote"])
            tracked.setdefault("notified", []).append(route)
            tracked["breakout_pending"] = None
            row["status"] = "PRICE_REASSESSMENT_ALERT_SENT"
            row["route"] = route
            row["trigger_bar_open_time"] = bar_time
        else:
            row["status"] = "NO_QUALIFIED_PRICE_PATTERN"
        report["signals"].append(row)
    save(STATE, state)
    report["counts"] = {"watchlist": len(signals),
                        "need_news_review": sum(x.get("status") in (
                            "REASSESS_NEWS_FIRST", "NEWS_COVERAGE_NOT_VERIFIED")
                            for x in report["signals"]),
                        "price_alerts": sum(x.get("status") == "PRICE_REASSESSMENT_ALERT_SENT"
                                            for x in report["signals"])}
    save(ASSESSMENT, report)
    print("Hourly full setup/news watch:", report["counts"], "coverage", report["news_coverage"])


if __name__ == "__main__":
    try:
        watch()
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print("Watch failed:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)

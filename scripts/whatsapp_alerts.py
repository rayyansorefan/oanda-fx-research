#!/usr/bin/env python3
"""Optional opt-in WhatsApp delivery of ORIGINAL approved M15 FX research alerts.

No live orders. Disabled until a WhatsApp Business sender, Meta-approved
template, verified recipient opt-in, and private Actions secrets exist.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import signal_watch

ASSESSMENT = Path("data/hourly_assessment.json")
SNAPSHOT = Path("data/latest.json")
SIGNALS = Path("signals.json")
NEWS = Path("data/news_latest.json")
STATE = Path("data/whatsapp_sent_state.json")
HEALTH = Path("data/whatsapp_delivery.json")

REQUIRED = (
    "WHATSAPP_CLOUD_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_RECIPIENT_E164",
    "WHATSAPP_TEMPLATE_NAME",
)
DASHBOARD = "https://rayyansorefan.github.io/standalone-wallet-paper-agent/"
LONDON = ZoneInfo("Europe/London")
ALLOWED_REVIEW = (
    "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES",
    "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES_WITH_GAPS",
)


def stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def read(path: Path, fallback: dict) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def configured(env: dict) -> tuple[bool, str]:
    if env.get("WHATSAPP_OPT_IN_CONFIRMED") != "yes":
        return False, "OPT_IN_NOT_CONFIRMED"
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        return False, "MISSING_WHATSAPP_CONFIGURATION"
    if not re.fullmatch(r"[0-9]{5,22}", env["WHATSAPP_PHONE_NUMBER_ID"]):
        return False, "INVALID_SENDER_ID"
    if not re.fullmatch(r"\+?[1-9][0-9]{7,14}", env["WHATSAPP_RECIPIENT_E164"]):
        return False, "INVALID_RECIPIENT_FORMAT"
    if not re.fullmatch(r"[a-z0-9_]{2,100}", env["WHATSAPP_TEMPLATE_NAME"]):
        return False, "INVALID_TEMPLATE_NAME"
    if not re.fullmatch(r"v[0-9]{1,2}\.[0-9]{1,2}",
                        env.get("WHATSAPP_GRAPH_VERSION", "v23.0")):
        return False, "INVALID_GRAPH_VERSION"
    if not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?",
                        env.get("WHATSAPP_TEMPLATE_LANGUAGE", "en_GB")):
        return False, "INVALID_TEMPLATE_LANGUAGE"
    return True, "READY"


def awake(now: datetime) -> bool:
    # 07:00 inclusive – 23:00 exclusive, automatically follows BST/GMT.
    local = now.astimezone(LONDON)
    return 7 <= local.hour < 23


def fmt(value) -> str:
    if value is None:
        return "none"
    try:
        return format(float(value), ".6g")
    except (TypeError, ValueError):
        return "unavailable"


def payload(signal: dict, row: dict, env: dict) -> dict:
    route = row["route"]
    open_bar = signal_watch.parse_time(row["trigger_bar_open_time"])
    close_bar = open_bar + timedelta(minutes=15)
    close_clock = close_bar.astimezone(LONDON).strftime("%d %b %H:%M UK")
    expiry = signal_watch.parse_time(signal["expires_at"]).astimezone(LONDON)
    expiration = expiry.strftime("%d %b %H:%M UK")
    values = [
        signal["instrument"].replace("_", "/"),
        signal["direction"],
        f"{route} · M15 close {close_clock}",
        fmt(signal["planned_entry"]),
        fmt(signal["stop"]),
        fmt(signal["tp1"]),
        fmt(signal.get("tp2")),
        expiration,
    ]
    return {
        "messaging_product": "whatsapp",
        "to": env["WHATSAPP_RECIPIENT_E164"].lstrip("+"),
        "type": "template",
        "template": {
            "name": env["WHATSAPP_TEMPLATE_NAME"],
            "language": {"code": env.get("WHATSAPP_TEMPLATE_LANGUAGE", "en_GB")},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(v)} for v in values],
            }],
        },
    }


def send(body: dict, token: str, phone_id: str, graph_version: str) -> bool:
    """Return True on Meta acceptance, never claim handset delivery."""
    url = f"https://graph.facebook.com/{graph_version}/{phone_id}/messages"
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=True).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=15) as response:
        result = json.load(response)
    # Graph API acceptance ≠ delivered; delivery confirmation needs webhooks.
    return bool(result.get("messages") and result["messages"][0].get("id"))


def eligible(row: dict, approved: dict, snapshot: dict, news: dict,
             assessment: dict, now: datetime) -> tuple[dict | None, str]:
    if row.get("status") != "PRICE_REASSESSMENT_ALERT_SENT":
        return None, "NOT_AN_APPROVED_PRICE_TRIGGER"
    if row.get("news_status") not in ALLOWED_REVIEW:
        return None, "NEWS_NOT_CLEARED"
    if not row.get("id") or row["id"] not in approved:
        return None, "NO_ORIGINAL_APPROVED_PLAN"
    if row.get("route") not in ("A", "B") or not row.get("trigger_bar_open_time"):
        return None, "NO_COMPLETED_M15_CONFIRMATION"
    plan = approved[row["id"]]
    if plan.get("instrument") != row.get("pair") or plan.get("direction") != row.get("direction"):
        return None, "SETUP_IDENTITY_MISMATCH"
    if signal_watch.parse_time(plan["expires_at"]) <= now:
        return None, "EXPIRED"
    try:
        checked = signal_watch.parse_time(assessment["collected_at"])
        collected = signal_watch.parse_time(snapshot["collected_at"])
        fetched = signal_watch.parse_time(news["collected_at"])
        closed = signal_watch.parse_time(row["trigger_bar_open_time"]) + timedelta(minutes=15)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, "UNVERIFIED_TIMESTAMPS"
    # Delivery is for newly observed confirmations, not replayed signals.
    if min((now - checked).total_seconds(), (now - collected).total_seconds()) < -90:
        return None, "FUTURE_SNAPSHOT"
    if (now - checked) > timedelta(minutes=12) or (now-collected) > timedelta(minutes=15):
        return None, "STALE_ANALYSIS"
    if now - fetched > timedelta(minutes=15) or now < fetched-timedelta(seconds=90):
        return None, "STALE_NEWS"
    if closed > now or now-closed > timedelta(minutes=22):
        return None, "OLD_OR_UNFINISHED_CANDLE"
    pairdata = (snapshot.get("pairs") or {}).get(plan["instrument"])
    err = signal_watch.validate_approved_plan(plan, pairdata, now)
    if err:
        return None, "RISK_GATE_" + err
    if signal_watch.blacked_out(plan, now):
        return None, "HIGH_IMPACT_BLACKOUT"
    quote = pairdata["quote"]
    if now - signal_watch.parse_time(quote["time"]) > timedelta(minutes=5):
        return None, "QUOTE_NO_LONGER_CURRENT"
    if not quote.get("tradeable"):
        return None, "PAIR_NOT_TRADEABLE"
    if float(quote.get("spread_pips", 1e9)) > float(plan["max_oanda_spread_pips"]):
        return None, "SPREAD_TOO_WIDE"
    # Existing watcher validated original planned gross/net RR and ongoing
    # technical trigger. Still require independent broker bid/ask manual check.
    return plan, "ELIGIBLE_FOR_CONDITIONAL_WHATSAPP"


def run(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    result = {"checked_at": stamp(now), "status": "DISABLED",
              "alerts_sent": 0, "suppressed": 0, "channel": "WHATSAPP_CLOUD_API",
              "meaning": "API acceptance is not handset delivery or trade instruction"}
    try:
        env = os.environ
        ready, reason = configured(env)
        if not ready:
            result["status"] = reason
            return result
        if not awake(now):
            result["status"] = "QUIET_HOURS"
            return result
        assessment = read(ASSESSMENT, {})
        snapshot = read(SNAPSHOT, {})
        news = read(NEWS, {})
        original = read(SIGNALS, {"signals": []})
        previous = read(STATE, {"sent": []})
        sent = list(previous.get("sent") or [])
        originals = {x["id"]: x for x in original.get("signals", [])
                     if isinstance(x, dict) and x.get("id")}
        if assessment.get("status") != "OK" or snapshot.get("status") not in ("OK", "PARTIAL"):
            result["status"] = "SOURCE_UNVERIFIED"
            return result
        for row in assessment.get("signals", []):
            if result["alerts_sent"] >= 3:
                break
            if not isinstance(row, dict) or row.get("status") != "PRICE_REASSESSMENT_ALERT_SENT":
                continue
            key = "|".join(str(row.get(k, "")) for k in
                           ("id", "route", "trigger_bar_open_time"))
            if key in sent:
                result["suppressed"] += 1
                continue
            plan, why = eligible(row, originals, snapshot, news, assessment, now)
            if not plan:
                result["suppressed"] += 1
                print("WhatsApp entry withheld:", why)
                continue
            body = payload(plan, row, env)
            accepted = send(body, env["WHATSAPP_CLOUD_TOKEN"],
                            env["WHATSAPP_PHONE_NUMBER_ID"],
                            env.get("WHATSAPP_GRAPH_VERSION", "v23.0"))
            if not accepted:
                result["status"] = "META_DID_NOT_ACCEPT"
                return result
            sent.append(key)
            save(STATE, {"sent": sent[-300:]})
            result["alerts_sent"] += 1
            print("Meta accepted a CONDITIONAL research alert, delivery not verified.")
        result["status"] = "META_ACCEPTED" if result["alerts_sent"] else "NO_ELIGIBLE_NEW_SIGNAL"
        return result
    except Exception as exc:
        # Never print phone numbers, access tokens or provider response bodies.
        result["status"] = "SEND_ERROR"
        result["error_type"] = type(exc).__name__
        return result
    finally:
        save(HEALTH, result)


if __name__ == "__main__":
    status = run()
    print("WhatsApp research channel:", status["status"],
          "accepted:", status["alerts_sent"])
    sys.exit(1 if status["status"] in ("SEND_ERROR", "META_DID_NOT_ACCEPT") else 0)

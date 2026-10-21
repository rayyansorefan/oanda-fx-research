#!/usr/bin/env python3
"""Private, opt-in WhatsApp Business research alert delivery; never trade.

Only sends approved WhatsApp *templates*, not arbitrary outbound text.
Credentials/recipient exist exclusively as private GitHub Actions secrets.
No personal identifiers, secrets or recipient numbers in repo/log/status.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPORT = Path("data/hourly_assessment.json")
SIGNALS = Path("signals.json")
STATE = Path("data/whatsapp_alert_state.json")
STATUS = Path("data/whatsapp_delivery_status.json")
MARKET = Path("data/latest.json")
TEMPLATE_DEFAULT = "fx_research_alert"
GRAPH_API_VERSION = "v23.0"
DASHBOARD_URL = "https://rayyansorefan.github.io/standalone-wallet-paper-agent/"
UK = ZoneInfo("Europe/London")
KIND = {
    "PRICE_REASSESSMENT_ALERT_READY": "POTENTIAL_ENTRY",
    "PRICE_REASSESSMENT_ALERT_SENT": "POTENTIAL_ENTRY",  # historical backward compatibility
    "REASSESS_NEWS_FIRST": "NEWS_REVIEW",
    "MANUAL_EVENT_BLACKOUT": "BLACKOUT",
    "REASSESS_OR_EXPIRE_PRIOR_PLAN_LEVEL_REACHED": "PLAN_INVALIDATED",
    "EXPIRED_OR_FUTURE": "EXPIRED",
    "INVALID_SETUP": "INVALID_SETUP",
}
MAX_AGE = timedelta(minutes=20)
MAX_EVENTS = 3
MAX_HISTORY = 250

def parse_datetime(value):
    if not isinstance(value, str):
        raise ValueError("Datetime missing")
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("Datetime must be offset-aware")
    return d.astimezone(timezone.utc)

def quiet_hours(now):
    hour = now.astimezone(UK).hour
    return hour >= 23 or hour < 7

def read(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback

def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2)+"\n", encoding="utf-8")

def emit_status(status, detail, sent=0, pending=0):
    write(STATUS, {"checked_at": datetime.now(timezone.utc).isoformat(),
                   "status": status, "detail": detail, "accepted_requests": sent,
                   "pending_alerts": pending, "delivery_confirmed": False})
    print("WhatsApp FX alerts:", status, "accepted", sent, "pending", pending)

def in_blackout(signal, now):
    for window in signal.get("event_blackouts_utc") or []:
        try:
            if parse_datetime(window["start"]) <= now <= parse_datetime(window["end"]):
                return True
        except (ValueError, KeyError, TypeError):
            return True
    return False

def is_worthy(row, signal, quote, now):
    kind=KIND.get(row.get("status"))
    if not kind or not signal:
        return None
    if signal.get("id") != row.get("id"):
        return None
    if kind == "POTENTIAL_ENTRY":
        # Revalidate that the canonical *original* plan still exists and
        # genuinely passes all published gates. Do not infer a current
        # Pepperstone fill merely because an M15 candle crossed a level.
        if not quote or not quote.get("tradeable"):
            return None
        try:
            from scripts.signal_watch import valid_risk
            t=parse_datetime(quote["time"])
            if now - t > timedelta(minutes=5) or t > now + timedelta(minutes=5):
                return None
            allowed=float(signal["max_oanda_spread_pips"])
            spread=float(quote["spread_pips"])
            if spread>allowed:
                return None
            if not valid_risk(signal,allowed,quote.get("pip_size")):
                return None
            if in_blackout(signal,now):
                return None
            if not parse_datetime(signal["created_at"]) <= now < parse_datetime(signal["expires_at"]):
                return None
            if row.get("route") not in ("A","B") or not row.get("trigger_bar_open_time"):
                return None
            candle_close = parse_datetime(row["trigger_bar_open_time"]) + timedelta(minutes=15)
            if candle_close > now or now - candle_close > timedelta(minutes=25):
                # An old/future M15 close is never an actionable entry alert.
                return None
            if row.get("news_status") not in (
                "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES",
                "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES_WITH_GAPS",
            ):
                return None
        except (ValueError,TypeError,KeyError,OverflowError):
            return None
    # No positive alert from a risk/coverage report; those are review warnings.
    return kind

def build_events(report, signals, market, now):
    if report.get("status") != "OK":
        return []
    try:
        checked=parse_datetime(report["collected_at"])
        source=parse_datetime(report["oanda_collected_at"])
        news=parse_datetime(report["news_collected_at"])
        if now-checked>MAX_AGE or checked>now+timedelta(minutes=5):
            return []
        if now-source>MAX_AGE or now-news>MAX_AGE:
            return []
    except (ValueError,KeyError):
        return []
    listed={s.get("id"):s for s in signals.get("signals",[]) if isinstance(s,dict)}
    out=[]
    for row in report.get("signals",[]):
        if not isinstance(row,dict):
            continue
        s=listed.get(row.get("id"))
        if not s:
            continue
        quote=(market.get("pairs",{}).get(s.get("instrument"),{}) or {}).get("quote",{})
        kind=is_worthy(row,s,quote,now)
        if kind is None:
            continue
        instrument=s.get("instrument","").replace("_","/")
        direction=s.get("direction","")
        if not re.fullmatch(r"[A-Z]{3}/[A-Z]{3}",instrument) or direction not in ("LONG","SHORT"):
            continue
        if kind=="POTENTIAL_ENTRY":
            summary=f"{instrument}: reassess potential {direction} (M15 route {row['route']})"
            detail=(f"OANDA plan {s['planned_entry']}; SL {s['stop']}; TP1 {s['tp1']}. "
                    "Verify current Pepperstone bid/ask, completed M15 and macro risk.")
            identity=f"{s['id']}|{kind}|{row['route']}|{row['trigger_bar_open_time']}"
        else:
            cause=row.get("reason") or row.get("news_status") or kind
            summary=f"{instrument}: {direction} {kind.replace('_',' ').lower()}"
            detail=f"Review/cancel plan. Reason {str(cause)[:80]}. Do not enter without new research."
            src=[x.get("id") for x in row.get("relevant_headlines",[]) if isinstance(x,dict)]
            src += [x.get("time") for x in row.get("release_blackouts",[]) if isinstance(x,dict)]
            suffix="|".join(str(x)[:55] for x in src[:3]) or str(row.get("reason") or kind)
            identity=f"{s['id']}|{kind}|{suffix}"
        out.append({"key": hashlib.sha256(identity.encode()).hexdigest(),
                    "kind":kind,"summary":summary[:180],"details":detail[:350]})
    return out[:MAX_EVENTS]

def outbound_template(event, recipient, template_name, language):
    # Three body parameters. Template MUST have exactly these body variables,
    # be approved by Meta and match language. No user number in content.
    return {"messaging_product":"whatsapp","to":recipient,
            "type":"template",
            "template":{"name":template_name,"language":{"code":language},
                        "components":[{"type":"body","parameters":[
                            {"type":"text","text":event["summary"]},
                            {"type":"text","text":event["details"]},
                            {"type":"text","text":DASHBOARD_URL}
                        ]}]}}

def send_message(event,config,transport=None):
    recipient=config["FX_WA_RECIPIENT"].strip()
    if not re.fullmatch(r"[1-9]\d{7,14}",recipient):
        raise ValueError("WhatsApp recipient secret must be E.164 digits without +")
    sender=config["FX_WA_PHONE_NUMBER_ID"].strip()
    if not re.fullmatch(r"\d{6,30}",sender):
        raise ValueError("Invalid WhatsApp sender ID")
    template=config["FX_WA_TEMPLATE_NAME"].strip()
    if not re.fullmatch(r"[a-z0-9_]{3,150}",template):
        raise ValueError("Invalid approved template name")
    language=config.get("FX_WA_LANGUAGE") or "en_GB"
    if not re.fullmatch(r"[a-z]{2}_[A-Z]{2}",language):
        raise ValueError("Invalid template language")
    body=json.dumps(outbound_template(event,recipient,template,language)).encode("utf-8")
    url="https://graph.facebook.com/"+GRAPH_API_VERSION+"/"+sender+"/messages"
    req=urllib.request.Request(url,body,method="POST",headers={
        "Authorization":"Bearer "+config["FX_WA_ACCESS_TOKEN"],
        "Content-Type":"application/json",
        "User-Agent":"FX-Research-Alerts/1.0"})
    response=(transport or urllib.request.urlopen)(req,timeout=15)
    with response:
        parsed=json.load(response)
    if not (parsed.get("messages") and parsed["messages"][0].get("id")):
        raise ValueError("WhatsApp did not return a message identifier")
    # Success means Meta accepted a request, NOT device delivery.
    return True

def main():
    now=datetime.now(timezone.utc)
    if quiet_hours(now):
        emit_status("QUIET_HOURS","UK notifications withheld 23:00–07:00")
        return 0
    report=read(REPORT,{})
    signals=read(SIGNALS,{"signals":[]})
    market=read(MARKET,{})
    events=build_events(report,signals,market,now)
    wanted=("FX_WA_ACCESS_TOKEN","FX_WA_PHONE_NUMBER_ID",
            "FX_WA_RECIPIENT","FX_WA_TEMPLATE_NAME")
    config={x:os.environ.get(x,"") for x in wanted}
    config["FX_WA_LANGUAGE"]=os.environ.get("FX_WA_LANGUAGE","en_GB")
    # No signals is NOT evidence of WhatsApp delivery readiness. Check the
    # encrypted sender/recipient/template configuration on every run.
    if not all(config.get(x) for x in wanted):
        emit_status("NOT_CONFIGURED","Private sender/recipient/approved-template credentials missing; no messages sent.",pending=len(events))
        return 0
    if not events:
        emit_status("NO_ALERTS","Sender configuration present; zero qualified alerts. No delivery test performed.")
        return 0
    state=read(STATE,{"sent_keys":[]})
    sent=set(state.get("sent_keys",[]))
    accepted=0
    for ev in events:
        if ev["key"] in sent:
            continue
        try:
            send_message(ev,config)
        except (ValueError,urllib.error.HTTPError,urllib.error.URLError,
                TimeoutError,OSError,TypeError,KeyError) as e:
            # Do not print Meta response bodies: may include phone numbers.
            emit_status("SEND_FAILED",type(e).__name__,accepted,len(events)-accepted)
            write(STATE,state)
            return 1
        state.setdefault("sent_keys",[]).append(ev["key"])
        state["sent_keys"]=state["sent_keys"][-MAX_HISTORY:]
        sent.add(ev["key"])
        write(STATE,state)
        accepted+=1
    emit_status("ACCEPTED_BY_META" if accepted else "ALREADY_REPORTED",
                "Accepted by WhatsApp API, not independently delivered." if accepted else "Duplicate alerts suppressed.",
                accepted)
    return 0

if __name__=="__main__":
    sys.exit(main())

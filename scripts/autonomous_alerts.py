#!/usr/bin/env python3
"""Autonomous, read-only FX alert lifecycle calculation.

Source of truth: approved immutable signals.json, completed OANDA research
snapshot and hourly watcher. Emits data/alerts_latest.json, never trades, sends
orders, calls a paid API, or claims Pepperstone execution.
"""
from __future__ import annotations
import datetime as dt
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/alerts_latest.json"

def parse(v):
    try:
        d=dt.datetime.fromisoformat(str(v).replace("Z","+00:00"))
        if d.tzinfo is None:
            return None
        return d.astimezone(dt.timezone.utc)
    except (ValueError,TypeError):
        return None

def number(v):
    try:
        if v is None or isinstance(v,bool):return None
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):
        return None

def safe_fresh(t,now,minutes):
    d=parse(t)
    return d is not None and dt.timedelta(minutes=-3)<=now-d<=dt.timedelta(minutes=minutes)

def event_blackout(signal,news,now):
    for item in signal.get("event_blackouts_utc",[]) or []:
        start,end=parse(item.get("start")),parse(item.get("end"))
        if start and end and start<=now<=end:
            return True
    currencies=str(signal.get("instrument","")).split("_")
    for event in (news.get("calendar") or {}).get("events",[]) or []:
        if event.get("currency") not in currencies or str(event.get("impact","")).lower()!="high":
            continue
        when=parse(event.get("time"))
        if when and abs((now-when).total_seconds())<=1800:
            return True
    return False

def evaluate(summary,news,approved,assessment,previous=None,now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:raise ValueError("UTC aware time required")
    previous=previous or {}
    old={a["id"]:a for a in previous.get("active_alerts",[]) if isinstance(a,dict) and a.get("id")}
    timestamp=now.isoformat().replace("+00:00","Z")
    output={"schema_version":1,"checked_at":timestamp,
        "source_snapshot_at":summary.get("collected_at"),
        "news_checked_at":news.get("collected_at"),
        "news_status":news.get("status","UNAVAILABLE"),
        "market_coverage":"OANDA PRACTICE ONLY; PEPPERSTONE BID/ASK UNVERIFIED",
        "active_alerts":[],"changes":[],"status":"OK"}
    snapshot_fresh=(summary.get("status") in ("OK","PARTIAL") and safe_fresh(summary.get("collected_at"),now,70))
    news_fresh=safe_fresh(news.get("collected_at"),now,70)
    if not snapshot_fresh:output["status"]="STALE_SOURCE"
    elif not news_fresh:output["status"]="STALE_NEWS"
    signals=approved.get("signals",[]) or []
    if not isinstance(signals,list) or len(signals)>3:raise ValueError("Invalid approved watcher")
    watched={str(x.get("id")):x for x in assessment.get("signals",[]) if isinstance(x,dict)}
    for signal in signals:
        if not isinstance(signal,dict):continue
        ident=str(signal.get("id","")).strip()
        pair=str(signal.get("instrument",""))
        direction=str(signal.get("direction",""))
        if not ident or direction not in ("LONG","SHORT") or pair not in (summary.get("pairs") or {}):continue
        stage="WATCH";reason="No independently confirmed fresh entry trigger."
        source=summary["pairs"][pair]
        quote=source.get("quote") or {}
        expires=parse(signal.get("expires_at"))
        created=parse(signal.get("created_at"))
        if not created or not expires or created>=expires or expires<=now:
            stage="CANCEL";reason="Original plan expired or timestamps invalid."
        elif not snapshot_fresh or not news_fresh:
            stage="BLOCK";reason="Hourly research or news source stale; entry blocked."
        elif not safe_fresh(quote.get("time"),now,20) or quote.get("tradeable") is not True:
            stage="BLOCK";reason="OANDA quote stale or non-tradeable; entry blocked."
        elif event_blackout(signal,news,now):
            stage="STAND_DOWN";reason="High-impact event blackout; no new entry."
        else:
            plan=[number(signal.get(k)) for k in ("planned_entry","stop","tp1","max_oanda_spread_pips")]
            entry,stop,target,pip_limit=plan
            pip=number(quote.get("pip_size"))
            side=1 if direction=="LONG" else -1
            if any(x is None for x in plan) or pip is None:
                stage="BLOCK";reason="Required plan risk fields unavailable."
            else:
                risk=side*(entry-stop);reward=side*(target-entry);spread=pip_limit*pip
                if risk<=0 or reward<=0 or pip_limit<0 or reward/risk<1.5 or (reward-spread)/(risk+spread)<1.35:
                    stage="BLOCK";reason="Original stop, target or net risk/reward invalid."
                else:
                    w=watched.get(ident,{})
                    result=w.get("status","")
                    if result=="PRICE_REASSESSMENT_ALERT_SENT":
                        stage="REVIEW_ENTRY";reason="Original M15 pattern signalled; check broker price, news and M15 evidence before any manual trade."
                    elif result in ("REASSESS_NEWS_FIRST","NEWS_COVERAGE_NOT_VERIFIED","MANUAL_EVENT_BLACKOUT"):
                        stage="STAND_DOWN";reason="News risk or coverage prevents a price-only entry alert."
                    elif result in ("REASSESS_OR_EXPIRE_PRIOR_PLAN_LEVEL_REACHED","INVALID_SETUP","EXPIRED_OR_FUTURE"):
                        stage="CANCEL";reason="Hourly watcher invalidated original research plan."
                    elif result in ("PAIR_M15_CANDLES_UNAVAILABLE",):
                        stage="BLOCK";reason="Completed M15 candle evidence missing."
                    else:
                        bid,ask=number(quote.get("bid")),number(quote.get("ask"))
                        atr=number((source.get("frames") or {}).get("M15",{}).get("indicators",{}).get("atr14"))
                        levels=[number(signal.get(key)) for key in ("alert_a","alert_b")]
                        levels=[x for x in levels if x is not None]
                        if bid is not None and ask is not None and levels and atr is not None and atr>0:
                            midpoint=(bid+ask)/2
                            if min(abs(midpoint-x) for x in levels)<=0.5*atr:
                                stage="APPROACHING";reason="Indicative OANDA quote is near an original alert level; M15 trigger not confirmed."
        record={"id":ident,"instrument":pair,"direction":direction,
            "stage":stage,"reason":reason,"expires_at":signal.get("expires_at"),
            "action":"REASSESS ONLY — NEVER AUTO-TRADE",
            "checked_at":timestamp}
        output["active_alerts"].append(record)
        if old.get(ident,{}).get("stage")!=stage:
            output["changes"].append(record)
    output["counts"]={key:sum(a["stage"]==key for a in output["active_alerts"])
        for key in ("APPROACHING","REVIEW_ENTRY","STAND_DOWN","CANCEL","BLOCK","WATCH")}
    return output

def load(path,default=None):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:return {} if default is None else default

def main():
    result=evaluate(
        load(ROOT/"data/summary.json"),
        load(ROOT/"data/news_latest.json"),
        load(ROOT/"signals.json",{"signals":[]}),
        load(ROOT/"data/hourly_assessment.json"),
        load(OUT))
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print("AUTONOMOUS FX ALERTS", result["status"], result["counts"],"state_changes",len(result["changes"]))
if __name__=="__main__":main()

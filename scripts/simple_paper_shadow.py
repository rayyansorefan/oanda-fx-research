"""Separate, frozen, forward-only *experimental* simple FX momentum/pullback study.

Read-only OANDA observations. Never creates conditional manual approvals, broker
orders, provider delivery receipts, or inferred historical fills. It does NOT
alter the champion or the pre-registered v20 champion/challenger holdout.
"""
from __future__ import annotations
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from scripts.paper_trade_audit import source_bars
    from scripts.signal_admission import news_gate
except ModuleNotFoundError:
    from paper_trade_audit import source_bars
    from signal_admission import news_gate

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/simple_paper_shadow.json"
PROTOCOL = ROOT / "docs/validation_protocols/FX-SIMPLE-20261104-v1.json"
VERSION = "simple-shadow-v1"
START = "2026-11-05T07:00:00Z"
PAIRS = ("EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "USD_CAD",
         "AUD_USD", "NZD_USD", "EUR_JPY", "EUR_GBP", "GBP_JPY")
ROUTES = ("MOMENTUM_CONTINUATION", "TREND_PULLBACK")
TERMINAL = {"WIN_TP1", "LOSS_STOP", "TIME_EXIT", "EXPIRED_UNTRIGGERED",
            "MISSED_ENTRY", "INVALIDATED_PRE_ENTRY", "AMBIGUOUS_ENTRY_BAR",
            "AMBIGUOUS_EXIT_BAR", "DATA_GAP", "PORTFOLIO_EXPOSURE_BLOCKED"}
UTC = timezone.utc
MAX_REPLAYS_PER_SNAPSHOT = 8
MAX_HOLD_BARS = 96
MAX_POSITIONS = 3
MAX_PER_CURRENCY = 2


def when(value):
    try:
        x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return x.astimezone(UTC) if x.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None


def pip_size(pair):
    return .01 if pair.endswith("_JPY") else .0001


def evidence_blob():
    data = Path(__file__).read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def validate_protocol(protocol):
    if (protocol.get("id") != "FX-SIMPLE-20261104-v1"
        or protocol.get("holdout_start_at") != START
        or protocol.get("script_blob_sha1") != evidence_blob()
        or protocol.get("no_orders") is not True):
        raise ValueError("SIMPLE_SHADOW_PROTOCOL_NOT_FROZEN")


def close_to_end(bar, direction):
    mid = bar.get("mid") or {}
    try:
        op, high, low, close = (float(mid[k]) for k in ("o", "h", "l", "c"))
    except (ValueError, TypeError, KeyError):
        return False
    span = high - low
    return bool(span > 0 and
                (close > op if direction == "LONG" else close < op) and
                ((close - low) / span if direction == "LONG"
                 else (high - close) / span) >= .70)


def propose(summary, news, now):
    """All thresholds fixed before the first forward observation.

    H1 and M15 EMA20/50 define regime; no daily, ADX or DMI veto.
    Breakout / EMA20 touch are only a *setup*: entry requires a NEW completed
    M15 candle after this timestamp, and a later executable-side quote.
    """
    source_at = when(summary.get("collected_at"))
    if (summary.get("status") not in ("OK", "PARTIAL") or source_at is None
            or not timedelta(0) <= now - source_at <= timedelta(minutes=15)
            or now < when(START)):
        return []
    rows = []
    for pair in PAIRS:
        src = (summary.get("pairs") or {}).get(pair) or {}
        q = src.get("quote") or {}
        qt = when(q.get("time"))
        if (q.get("tradeable") is not True or qt is None or
                not timedelta(seconds=-60) <= now-qt <= timedelta(minutes=5)):
            continue
        bid, ask = num(q.get("bid")), num(q.get("ask"))
        pip = num(q.get("pip_size")) or pip_size(pair)
        if bid is None or ask is None or not (0 < bid < ask):
            continue
        spread = ask-bid
        if spread/pip > (3.0 if pair.endswith("_JPY") else 2.5):
            continue
        frames = src.get("frames") or {}
        m = (frames.get("M15") or {}).get("indicators") or {}
        h = (frames.get("H1") or {}).get("indicators") or {}
        ma, mb, ha, hb, atr = [num(v) for v in
            (m.get("ema20"), m.get("ema50"), h.get("ema20"),
             h.get("ema50"), m.get("atr14"))]
        if any(v is None for v in (ma, mb, ha, hb, atr)) or atr <= 0:
            continue
        direction = ("LONG" if ma > mb and ha > hb else
                     "SHORT" if ma < mb and ha < hb else None)
        if direction is None or spread > .20*atr:
            continue
        candles = (frames.get("M15") or {}).get("last_3_completed_candles") or []
        if len(candles) < 3:
            continue
        last_three = candles[-3:]
        last_time = when(last_three[-1].get("time"))
        if (last_time is None or last_time+timedelta(minutes=15)>now
                or now-(last_time+timedelta(minutes=15))>timedelta(minutes=25)):
            continue
        try:
            last = last_three[-1]["mid"]
            op, hi, lo, cl = (float(last[k]) for k in ("o", "h", "l", "c"))
        except (ValueError, KeyError, TypeError):
            continue
        if news_gate(pair, news, now):
            continue
        sign = 1 if direction=="LONG" else -1
        entry = ask if sign > 0 else bid
        risk = 1.25*atr
        # Geometric stop and target are fixed ATR multiples; no hindsight
        # selection of a distant target to manufacture positive reward/risk.
        decimals = 3 if pip >= .01 else 5
        stop = round(entry-sign*risk, decimals)
        target = round(entry+sign*1.8*risk, decimals)
        if not (stop<entry<target if sign>0 else target<entry<stop):
            continue
        net_rr = (abs(target-entry)-spread)/(abs(entry-stop)+spread)
        if net_rr < 1.35:
            continue
        for route in ROUTES:
            if route=="MOMENTUM_CONTINUATION":
                eligible = (close_to_end(last_three[-1],direction)
                            and sign*(cl-op) >= .35*atr
                            and abs(cl-ma) <= 1.25*atr)
            else:
                eligible = (lo<=ma<=hi and close_to_end(last_three[-1],direction)
                            and abs(cl-ma)<=.60*atr)
            if not eligible:
                continue
            trigger = (hi+pip if sign>0 else lo-pip)
            identifier = (VERSION+"-"+now.strftime("%Y%m%dT%H%M%SZ")+
                          "-"+pair+"-"+route)
            rows.append({
                "id":identifier, "policy":VERSION, "instrument":pair,
                "direction":direction, "setup_type":route, "created_at":iso(now),
                "expires_at":iso(now+timedelta(minutes=90)),
                "source_at":summary["collected_at"],"last_source_bar_at":iso(last_time),
                "trigger":round(trigger,decimals), "indicative_entry":round(entry,decimals),
                "stop":stop, "tp1":target, "pip_size":pip,
                "atr_at_decision":atr, "max_spread_pips":round(spread/pip*1.25,3),
                "max_chase_atr":.35, "manual_response_seconds":60,
                "gross_rr":round(abs(target-entry)/abs(entry-stop),4),
                "estimated_net_rr_before_slippage":round(net_rr,4),
                "experimental_paper_only":True, "broker_order_permitted":False,
                "entry_condition":"NEXT_COMPLETED_M15_CLOSE_BEYOND_FROZEN_TRIGGER",
                "invalidation":"PREENTRY_STOP_TOUCH_OR_EXPIRY",
            })
    return rows


def _ceil_m15(moment):
    midnight=moment.replace(minute=0,second=0,microsecond=0)
    minutes = (moment-midnight).total_seconds()/60
    import math as _math
    return midnight+timedelta(minutes=15*_math.ceil(minutes/15))


def exit_r(plan, fill, price):
    return ((price-fill) if plan["direction"]=="LONG" else
            (fill-price)) / abs(fill-plan["stop"])


def settle(plan, result, price, at, status):
    fill=result["entry"]
    risk=abs(fill-plan["stop"])
    pip=plan["pip_size"]
    base=exit_r(plan,fill,price)
    # Spread already present in ask/bid: do not subtract it twice.
    # Extra costs are unverified scenarios, not Pepperstone P&L.
    stress=(2*.25+2*.15)*pip/risk
    return {**result,"status":status,"at":iso(at),"exit":price,
            "scored":True,"net_r_bidask":round(base,6),
            "net_r_stressed_illustrative":round(base-stress,6),
            "broker_costs_verified":False,
            "financing_cost_unknown":True}


def replay(plan,bars,now,observations):
    """Deterministic M15 replay; never enter on confirmation's own candle."""
    created,expiry=when(plan["created_at"]),when(plan["expires_at"])
    if created is None or expiry is None or now<created:
        return {"status":"UNTESTABLE","scored":False}
    candles=sorted((b for b in bars if b["t"]>=created and
                    b["t"]+timedelta(minutes=15)<=now),key=lambda b:b["t"])
    if not candles or candles[0]["t"]>created+timedelta(minutes=15):
        return {"status":"DATA_GAP","scored":False,"reason":"MISSING_INITIAL_M15"}
    sign=1 if plan["direction"]=="LONG" else -1
    stage="TRIGGER"
    prior=None
    result={"status":"PENDING_TRIGGER","scored":False}
    available=None
    holding=0
    for b in candles:
        t=b["t"]
        close_at=t+timedelta(minutes=15)
        if prior and t-prior>timedelta(minutes=15,seconds=1):
            weekend=(prior.weekday() in (4,5,6) and t-prior>=timedelta(hours=24))
            if not weekend:
                return {**result,"status":"DATA_GAP","scored":False,
                        "reason":"MISSING_POSTPLAN_M15"}
        prior=t
        mid=b["mid"]
        entry_side=b["ask"] if sign>0 else b["bid"]
        exit_side=b["bid"] if sign>0 else b["ask"]
        if stage=="TRIGGER":
            if close_at>expiry:
                break
            if (exit_side["l"]<=plan["stop"] if sign>0 else
                exit_side["h"]>=plan["stop"]):
                return {"status":"INVALIDATED_PRE_ENTRY","scored":False,
                        "reason":"STOP_BEFORE_TRIGGER","at":iso(close_at)}
            trigger_met=(mid["c"]>plan["trigger"] if sign>0 else
                         mid["c"]<plan["trigger"])
            if not trigger_met or not close_to_end({"mid":mid},plan["direction"]):
                continue
            if (b["ask"]["c"]-b["bid"]["c"])/plan["pip_size"]>plan["max_spread_pips"]:
                continue
            observed=next((o for o in sorted(observations) if
                           o>=close_at and o<=now),None)
            result={"status":"TRIGGERED_UNFILLED","scored":False,
                    "triggered_at":iso(close_at),
                    "detected_at":iso(observed) if observed else None}
            if observed is None:
                if now>expiry:
                    return {**result,"status":"MISSED_ENTRY",
                            "reason":"NO_PAIR_SPECIFIC_OBSERVATION"}
                return result
            if observed-close_at>timedelta(minutes=15):
                return {**result,"status":"MISSED_ENTRY",
                        "reason":"LATE_OBSERVATION"}
            available=_ceil_m15(observed+timedelta(seconds=plan["manual_response_seconds"]))
            result["entry_available_at"]=iso(available)
            if available>=expiry:
                return {**result,"status":"MISSED_ENTRY","reason":"ENTRY_EXPIRED"}
            stage="FILL"
            continue
        if stage=="FILL":
            if t<available:
                if (exit_side["l"]<=plan["stop"] if sign>0 else
                    exit_side["h"]>=plan["stop"]):
                    return {**result,"status":"INVALIDATED_PRE_ENTRY",
                            "reason":"STOP_BEFORE_EXECUTION"}
                continue
            if t>available+timedelta(seconds=1):
                return {**result,"status":"DATA_GAP","scored":False,
                        "reason":"NEXT_M15_OPEN_UNOBSERVED"}
            spread=(b["ask"]["o"]-b["bid"]["o"])/plan["pip_size"]
            fill=entry_side["o"]
            if (spread>plan["max_spread_pips"]
                or abs(fill-plan["indicative_entry"])>plan["max_chase_atr"]*plan["atr_at_decision"]
                or not (plan["stop"]<fill<plan["tp1"] if sign>0
                        else plan["tp1"]<fill<plan["stop"])):
                return {**result,"status":"MISSED_ENTRY","reason":"SPREAD_OR_CHASING"}
            risk=abs(fill-plan["stop"])
            if risk==0 or (abs(plan["tp1"]-fill)-(b["ask"]["o"]-b["bid"]["o"]))/(risk+b["ask"]["o"]-b["bid"]["o"])<1.35:
                return {**result,"status":"MISSED_ENTRY","reason":"NET_RISK_FAILS_AT_FILL"}
            result={**result,"status":"OPEN","entry_at":iso(t),"entry":fill,
                    "risk_initial":risk,"scored":False}
            stop_hit=(exit_side["l"]<=plan["stop"] if sign>0 else
                      exit_side["h"]>=plan["stop"])
            target_hit=(exit_side["h"]>=plan["tp1"] if sign>0 else
                        exit_side["l"]<=plan["tp1"])
            if stop_hit or target_hit:
                return {**result,"status":"AMBIGUOUS_ENTRY_BAR",
                        "scored":False,"reason":"ENTRY_BAR_EXIT_EXTREME"}
            stage="OPEN"
            continue
        if stage=="OPEN":
            holding+=1
            at_open=exit_side["o"]
            gap_stop=(at_open<=plan["stop"] if sign>0 else at_open>=plan["stop"])
            if gap_stop:
                return settle(plan,result,at_open,t,"LOSS_STOP")
            gap_tp=(at_open>=plan["tp1"] if sign>0 else at_open<=plan["tp1"])
            if gap_tp:
                return settle(plan,result,plan["tp1"],t,"WIN_TP1")
            stop_hit=(exit_side["l"]<=plan["stop"] if sign>0 else
                      exit_side["h"]>=plan["stop"])
            tp_hit=(exit_side["h"]>=plan["tp1"] if sign>0 else
                    exit_side["l"]<=plan["tp1"])
            if stop_hit and tp_hit:
                return {**result,"status":"AMBIGUOUS_EXIT_BAR","scored":False,
                        "at":iso(close_at)}
            if stop_hit or tp_hit:
                return settle(plan,result,plan["stop"] if stop_hit else plan["tp1"],
                              close_at,"LOSS_STOP" if stop_hit else "WIN_TP1")
            if holding>=MAX_HOLD_BARS:
                return settle(plan,result,exit_side["c"],close_at,"TIME_EXIT")
    if stage=="OPEN":
        return result
    if now>expiry:
        return {**result,"status":("EXPIRED_UNTRIGGERED" if stage=="TRIGGER"
                                  else "MISSED_ENTRY"),"scored":False}
    return result


def apply_portfolio_limits(records):
    """Chronological deterministic shadow portfolio; never choose by P&L."""
    filled=sorted(((p["result"]["entry_at"],key,p) for key,p in records.items()
                   if (p.get("result") or {}).get("entry_at") and
                   p["result"]["status"] in ("OPEN","WIN_TP1","LOSS_STOP","TIME_EXIT")),
                  key=lambda row:(row[0],row[1]))
    accepted=[]
    for entered,key,row in filled:
        at=when(entered)
        active=[r for r in accepted if r["entry"]<=at and
                (r["closed"] is None or r["closed"]>at)]
        pair=row["plan"]["instrument"]
        currencies=pair.split("_")
        reason=("TOTAL_CONCURRENT_LIMIT" if len(active)>=MAX_POSITIONS else
                "DUPLICATE_OPEN_PAIR" if any(r["pair"]==pair for r in active) else
                "CURRENCY_CONCENTRATION" if any(
                    sum(c in r["pair"].split("_") for r in active)>=MAX_PER_CURRENCY
                    for c in currencies) else None)
        if reason:
            result=row["result"]
            row.setdefault("blocked_counterfactual",result)
            row["result"]={"status":"PORTFOLIO_EXPOSURE_BLOCKED","scored":False,
                           "reason":reason,"counterfactual_not_executed":True,
                           "entry_candidate_at":entered}
        else:
            accepted.append({"pair":pair,"entry":at,
                             "closed":when(row["result"].get("at"))})


def update(summary,news,book=None,fetch=None,*,now=None):
    now=when(now) if isinstance(now,str) else (now or datetime.now(UTC))
    if not now or now.tzinfo is None:
        raise ValueError("UTC study observation required")
    if book is None:
        book={"schema":1,"study_version":VERSION,"holdout_start_at":START,
              "registered_at":iso(now),"records":{},
              "pair_observations":{},"source_snapshots":[]}
    if book.get("study_version")!=VERSION or book.get("holdout_start_at")!=START:
        raise ValueError("FROZEN_SIMPLE_SHADOW_RULES_CHANGED")
    observed_at=summary.get("collected_at")
    snapshot_time=when(observed_at)
    if (snapshot_time and observed_at not in book["source_snapshots"]
        and snapshot_time<=now and snapshot_time>=when(START)):
        book["source_snapshots"].append(observed_at)
        for pair in (summary.get("pairs") or {}):
            if pair in PAIRS:
                book["pair_observations"].setdefault(pair,[]).append(iso(now))
        for plan in propose(summary,news,now):
            overlapping=any(row["plan"]["instrument"]==plan["instrument"] and
                            row["plan"]["setup_type"]==plan["setup_type"] and
                            when(row["plan"]["expires_at"])>now
                            for row in book["records"].values())
            if not overlapping and plan["id"] not in book["records"]:
                book["records"][plan["id"]]={"plan":plan,
                    "result":{"status":"PENDING_TRIGGER","scored":False}}
    if now<when(START):
        book["status"]="HOLDOUT_NOT_STARTED"
    else:
        pending=[row for row in book["records"].values() if
                 row["result"].get("status") not in TERMINAL]
        for item in sorted(pending,key=lambda x:x["plan"]["created_at"])[:MAX_REPLAYS_PER_SNAPSHOT]:
            plan=item["plan"]
            try:
                candles=(fetch(plan,now) if fetch else source_bars(plan,now))
                observations=[when(x) for x in book["pair_observations"].get(plan["instrument"],[])]
                item["result"]=replay(plan,candles,now,
                                      [o for o in observations if o is not None])
                if getattr(candles,"evidence_path",None):
                    item["result"]["source_evidence"]=candles.evidence_path
            except (OSError,RuntimeError,TypeError,ValueError,KeyError,TimeoutError) as exc:
                item["result"]={"status":"SOURCE_UNAVAILABLE","scored":False,
                                "reason":type(exc).__name__}
        apply_portfolio_limits(book["records"])
        book["status"]="FORWARD_RESEARCH_ONLY"
    book["checked_at"]=iso(now)
    counts=Counter(x["result"]["status"] for x in book["records"].values())
    scored=[x["result"] for x in book["records"].values()
            if x["result"].get("scored") is True]
    book["metrics"]={"plans":len(book["records"]),"outcomes":dict(counts),
       "filled":sum(bool(x["result"].get("entry_at")) for x in book["records"].values()),
       "scored_exits":len(scored),
       "open_paper_positions":counts["OPEN"],
       "net_total_r_bidask":round(sum(x["net_r_bidask"] for x in scored),5) if scored else None,
       "net_total_r_cost_stress":round(sum(x["net_r_stressed_illustrative"] for x in scored),5) if scored else None,
       "broker_costs_verified":False,
       "performance_validated":False,
       "benchmark":{"no_trade_r":0,"champion_v20":"SEPARATE_PAIRED_DAYS_REQUIRED"}}
    book["broker_orders_submitted"]=0
    book["automatic_promotion_allowed"]=False
    return book


def main():
    protocol=json.loads(PROTOCOL.read_text())
    validate_protocol(protocol)
    now=datetime.now(UTC)
    book=json.loads(OUT.read_text()) if OUT.exists() else None
    summary=json.loads((ROOT/"data/summary.json").read_text())
    news=json.loads((ROOT/"data/news_latest.json").read_text())
    result=update(summary,news,book,now=now)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("Simple forward-only shadow",result["status"],result["metrics"])


if __name__=="__main__":
    main()

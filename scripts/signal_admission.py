"""Prospective, risk-gated FX research signal handoff (never order execution).

Freezes a maximum of three independently justified conditional research plans
BEFORE their M15 entry candle. Never retroactively labels a fill or a win.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_PLANS = 3
EXCLUDED = {"GBP_ZAR"}

def moment(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None

def number(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None

def trend(ind):
    a, b = number(ind.get("ema20")), number(ind.get("ema50"))
    return "LONG" if a is not None and b is not None and a > b else (
        "SHORT" if a is not None and b is not None and a < b else None)

def news_gate(pair, news, now):
    if not isinstance(news, dict) or not moment(news.get("collected_at")):
        return "NEWS_NOT_VERIFIED"
    if abs((now - moment(news["collected_at"])).total_seconds()) > 25 * 60:
        return "NEWS_STALE"
    info = (news.get("by_pair") or {}).get(pair)
    if not isinstance(info, dict):
        return "PAIR_NEWS_MISSING"
    coverage = info.get("coverage") or {}
    if info.get("calendar_coverage") != "OK" or info.get("near_high_impact_calendar"):
        return "EVENT_CALENDAR_OR_BLACKOUT"
    if coverage.get("missing_required") or coverage.get("currencies_without_supported_official_feed"):
        return "PRIMARY_NEWS_SOURCE_MISSING"
    if coverage.get("status") not in ("OK", "LIMITED"):
        return "NEWS_COVERAGE_NOT_VERIFIED"
    # Material headlines require independent review, not heuristic clearance.
    for item in info.get("recent_relevant_headlines") or []:
        published = moment(item.get("published_at"))
        if published and timedelta(0) <= now - published <= timedelta(hours=3):
            if item.get("severity") in ("REVIEW", "HIGH", "CRITICAL"):
                return "RECENT_MATERIAL_HEADLINE_REVIEW"
    return None

def qualify(pair, ranked, source, news, now, route):
    """Return (frozen prospective signal, rejection); never uses future candles."""
    if pair in EXCLUDED or route not in ("EMA20_PULLBACK", "MOMENTUM_CONTINUATION"):
        return None, "UNSUPPORTED_PAIR_OR_ROUTE"
    # Screen rank uses mid-price vs EMA for thematic bias; admission instead
    # follows the independently aligned M15/H1 EMA+DMI scouting direction.
    # Four-frame agreement, cost and actual risk geometry are still mandatory.
    direction = (ranked.get("deep_entry_scout_direction")
                 or ranked.get("potential_direction_not_approved"))
    if direction not in ("LONG", "SHORT"):
        return None, "NO_RESEARCH_DIRECTION"
    if (ranked.get("adaptive_context") or {}).get("block_new_alert"):
        return None, "REVERSAL_OR_EXHAUSTION"
    q, frames = source.get("quote") or {}, source.get("frames") or {}
    snapshot = moment(q.get("time"))
    if not snapshot or q.get("tradeable") is not True or not timedelta(seconds=-180) <= now-snapshot <= timedelta(minutes=20):
        return None, "STALE_OR_UNTRADEABLE_OANDA_QUOTE"
    bid, ask, pip, spread_pips = [number(q.get(k)) for k in ("bid", "ask", "pip_size", "spread_pips")]
    if None in (bid, ask, pip, spread_pips) or not (0 < bid < ask and pip > 0 and 0 < spread_pips <= 10):
        return None, "INVALID_BID_ASK_OR_SPREAD"
    indicators = {}
    for tf in ("M15", "H1", "H4", "D"):
        item = (frames.get(tf) or {}).get("indicators") or {}
        if trend(item) != direction:
            return None, "FOUR_FRAME_TREND_CONFLICT"
        indicators[tf] = item
    m, h = indicators["M15"], indicators["H1"]
    atr = number(m.get("atr14"))
    if not atr or atr <= 0 or spread_pips * pip > .50 * atr:
        return None, "SPREAD_TOO_HIGH_RELATIVE_TO_VOLATILITY"
    if any((number(i.get("adx14")) or 0) < 20 for i in (m, h)):
        return None, "ADX_TREND_STRENGTH"
    for i in (m, h):
        plus, minus = number(i.get("plus_di14")), number(i.get("minus_di14"))
        if plus is None or minus is None or not (
                plus > minus if direction == "LONG" else minus > plus):
            return None, "DMI_DIRECTION_CONFLICT"
    bars = (frames.get("M15") or {}).get("last_3_completed_candles") or []
    if len(bars) < 3:
        return None, "COMPLETED_M15_HISTORY_MISSING"
    last_open = moment(bars[-1].get("time"))
    if not last_open or last_open + timedelta(minutes=15) > now or now - (last_open + timedelta(minutes=15)) > timedelta(minutes=30):
        return None, "RECENT_COMPLETED_M15_MISSING"
    mids = [x.get("mid") or {} for x in bars[-3:]]
    try:
        swing_high = max(float(x["h"]) for x in mids)
        swing_low = min(float(x["l"]) for x in mids)
        candle = mids[-1]
        opening, close = float(candle["o"]), float(candle["c"])
        high, low = float(candle["h"]), float(candle["l"])
    except (TypeError, ValueError, KeyError):
        return None, "INVALID_M15_PRICE_STRUCTURE"
    spread = spread_pips * pip
    ema = number(m.get("ema20"))
    if not ema:
        return None, "EMA20_MISSING"
    live_side = ask if direction == "LONG" else bid
    if abs(live_side - ema) > 1.5 * atr and route == "EMA20_PULLBACK":
        return None, "PULLBACK_PRICE_TOO_FAR_FROM_ZONE"
    if route == "MOMENTUM_CONTINUATION":
        span = high - low
        body = abs(close - opening)
        close_strength = ((close - low) / span if direction == "LONG" else
                          (high - close) / span) if span > 0 else 0
        if span <= 0 or body / span < .55 or close_strength < .72:
            return None, "NO_DECISIVE_COMPLETED_M15_IMPULSE"
        if not (close > opening if direction == "LONG" else close < opening):
            return None, "IMPULSE_DIRECTION_CONFLICT"
        if abs(live_side - close) > .40 * atr:
            return None, "CONTINUATION_PRICE_STRETCHED"
        if direction == "LONG":
            level = high + pip
            entry = level + spread
        else:
            level = low - pip
            entry = level - spread
        alert = {"alert_c": level}
    else:
        entry, level = ema, ema
        alert = {"alert_b": level}
    # Swing-based stop, with ATR floor. The target is observed 20-bar
    # support/resistance, not a made-up multiple of the chosen stop.
    if direction == "LONG":
        stop = min(swing_low - .12 * atr, entry - 1.05 * atr)
        potential = sorted({float(i["resistance20"]) for i in indicators.values()
                            if number(i.get("resistance20")) is not None
                            and number(i.get("resistance20")) > entry})
    else:
        stop = max(swing_high + .12 * atr, entry + 1.05 * atr)
        potential = sorted({float(i["support20"]) for i in indicators.values()
                            if number(i.get("support20")) is not None
                            and number(i.get("support20")) < entry}, reverse=True)
    if not potential:
        return None, "NO_INDEPENDENT_STRUCTURE_TARGET"
    tp = potential[0]  # Nearest adverse structure is not skipped to boost reward.
    risk, reward = abs(entry - stop), abs(tp - entry)
    if risk < .75 * atr or risk > 3.5 * atr:
        return None, "UNREALISTIC_STRUCTURAL_STOP"
    spread_cap = min(10.0, spread_pips * 1.15)
    conservative_cost = spread_cap * pip
    gross = reward / risk
    net = (reward - conservative_cost) / (risk + conservative_cost)
    if gross < 1.50 or net < 1.35:
        return None, "NEAREST_STRUCTURE_TARGET_REWARD_INADEQUATE"
    reason = news_gate(pair, news, now)
    if reason:
        return None, reason
    # Freeze levels while the trigger remains in the future. The separate
    # hourly watcher verifies actual later completed bars, spread and news.
    decimals = 3 if pip >= .01 else 5
    now_utc = now.isoformat().replace("+00:00", "Z")
    deadline = (now + timedelta(minutes=90)).isoformat().replace("+00:00", "Z")
    identifier = "AUTO-" + now.strftime("%Y%m%dT%H%M") + "-" + pair + "-" + (
        "L" if direction == "LONG" else "S") + ("-C" if route == "MOMENTUM_CONTINUATION" else "-B")
    plan = {
        "id": identifier, "instrument": pair, "direction": direction,
        "created_at": now_utc, "expires_at": deadline,
        "planned_entry": round(entry, decimals), "stop": round(stop, decimals),
        "tp1": round(tp, decimals),
        "max_oanda_spread_pips": round(spread_cap, 3),
        "event_blackouts_utc": [], "entry_route": route,
        "oanda_snapshot_at": now_utc,
        "source_quote_at": q.get("time"),
        "research_only": True, "broker_verified": False,
        "account_equity_verified": False,
        "max_equity_risk_fraction": .0025,
        "manual_broker_check_required": True,
        "gross_rr_at_creation": round(gross, 3),
        "net_rr_at_spread_cap": round(net, 3),
        "technical_context": {
            "full_frame_alignment": True,
            "m15_adx": number(m.get("adx14")), "h1_adx": number(h.get("adx14")),
            "m15_plus_di": number(m.get("plus_di14")),
            "m15_minus_di": number(m.get("minus_di14")),
            "h1_plus_di": number(h.get("plus_di14")),
            "h1_minus_di": number(h.get("minus_di14")),
            "m15_atr": atr,
            "source_bar_time": bars[-1].get("time"),
        },
        "target_basis": "nearest completed M15/H1/H4/D 20-bar support/resistance",
        "trigger_basis": "future completed M15 only",
        **{k: round(v, decimals) for k, v in alert.items()}
    }
    # Reject rounding artefacts instead of hiding lower reward or bad direction.
    e, st, target = plan["planned_entry"], plan["stop"], plan["tp1"]
    if not ((st < e < target) if direction == "LONG" else (target < e < st)):
        return None, "ROUNDED_LEVELS_INVALID"
    cost = plan["max_oanda_spread_pips"] * pip
    if abs(target - e) / abs(e - st) < 1.5 or (abs(target - e) - cost) / (abs(e - st) + cost) < 1.35:
        return None, "ROUNDED_NET_REWARD_BELOW_MINIMUM"
    return plan, None

def admit(summary, news, previous, archive=None):
    now = moment(summary.get("collected_at"))
    audit = {"source_at": summary.get("collected_at"), "admitted": [],
             "rejected": {}, "active": 0, "status": "NO_SOURCE"}
    if not now or summary.get("status") not in ("OK", "PARTIAL"):
        return previous, archive or {"signals": []}, audit
    previous = previous if isinstance(previous, dict) else {"schema_version": 1, "signals": []}
    historic = archive if isinstance(archive, dict) else {"signals": []}
    already = list(previous.get("signals") or [])
    kept = []
    for item in already:
        if not isinstance(item, dict):
            continue
        expires = moment(item.get("expires_at"))
        if expires and expires > now:
            kept.append(item)  # Existing original plan is immutable.
        elif item.get("id") and not any(z.get("id") == item["id"] for z in historic["signals"]):
            historic["signals"].append(item)
    audit["active"] = len(kept)
    if len(kept) > MAX_PLANS:
        audit["status"] = "TOO_MANY_EXISTING_PLANS"
        return previous, historic, audit
    selected = set()
    for plan in kept:
        selected.add(plan.get("instrument"))
    candidates = sorted((x for x in summary.get("ranked_review_candidates") or []
                         if isinstance(x, dict)),
                        key=lambda z: -(number(z.get("research_priority")) or 0))
    for candidate in candidates:
        pair = candidate.get("instrument")
        if not pair or pair in selected:
            continue
        data = (summary.get("pairs") or {}).get(pair) or {}
        for route in ("MOMENTUM_CONTINUATION", "EMA20_PULLBACK"):
            proposal, error = qualify(pair, candidate, data, news, now, route)
            if error:
                audit["rejected"].setdefault(error, 0)
                audit["rejected"][error] += 1
                continue
            if len(kept) >= MAX_PLANS:
                break
            kept.append(proposal)
            selected.add(pair)
            audit["admitted"].append({"id": proposal["id"], "pair": pair,
                                     "direction": proposal["direction"],
                                     "route": route})
            break
        if len(kept) >= MAX_PLANS:
            break
    audit["active"] = len(kept)
    audit["status"] = "RESEARCH_PLANS_FROZEN" if kept else "NO_QUALIFIED_CONDITIONAL_PLAN"
    updated = {"schema_version": 1, "generated_at": now.isoformat().replace("+00:00", "Z"),
               "signals": kept,
               "note": "Conditional OANDA manual-research plans only. Never broker-verified or executable. Requires future completed trigger, renewed news/spread review and verified account risk <=0.25%. No orders."}
    return updated, historic, audit

def main():
    summary = json.loads((ROOT / "data/summary.json").read_text())
    news = json.loads((ROOT / "data/news_latest.json").read_text())
    path = ROOT / "signals.json"
    archive_path = ROOT / "data/signal_plan_archive.json"
    previous = json.loads(path.read_text()) if path.exists() else {"signals":[]}
    archive = json.loads(archive_path.read_text()) if archive_path.exists() else {"signals":[]}
    updated, archived, audit = admit(summary, news, previous, archive)
    (ROOT / "data/signal_admission_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n")
    if updated.get("signals") != previous.get("signals"):
        path.write_text(json.dumps(updated, indent=2) + "\n")
    if archived.get("signals") != archive.get("signals"):
        archive_path.write_text(json.dumps(archived, indent=2) + "\n")
    print("Conditional FX admission", audit["status"], "active", audit["active"],
          "new", len(audit["admitted"]), "rejected", audit["rejected"])

if __name__ == "__main__":
    main()

"""Prospective, risk-gated FX research signal handoff (never order execution).

Freezes a maximum of three independently justified conditional research plans
BEFORE their M15 entry candle. Never retroactively labels a fill or a win.
"""
from __future__ import annotations
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    from scripts.adaptive_research import entry_context
    from scripts.news_watch import requires_headline_review
except ModuleNotFoundError:  # executed directly from scripts/ by GitHub Actions
    from adaptive_research import entry_context
    from news_watch import requires_headline_review

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
            if requires_headline_review(item):
                return "RECENT_MATERIAL_HEADLINE_REVIEW"
    return None

def qualify(pair, ranked, source, news, now, route, *, policy="champion"):
    if policy not in ("champion", "challenger_v1"):
        raise ValueError("Unknown admission policy")
    paper_only = policy != "champion"
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
    q, frames = source.get("quote") or {}, source.get("frames") or {}
    # The thematic research rank can point in the OPPOSITE direction from
    # the independently aligned EMA/DMI scout. Re-evaluate exhaustion for
    # the signal's actual intended direction using the current source data.
    # Otherwise an unrelated old-direction warning silently vetoes it.
    current_context = entry_context({"quote": q, "frames": frames}, direction)
    extension = number(current_context.get("m15_ema_distance_atr"))
    moderate_extension = (
        route == "MOMENTUM_CONTINUATION"
        and current_context.get("status") == "REASSESS_EXTENDED"
        and extension is not None and 1.55 < extension <= 2.0
    )
    if current_context["block_new_alert"] and not moderate_extension:
        return None, "REVERSAL_OR_EXHAUSTION"
    snapshot = moment(q.get("time"))
    if not snapshot or q.get("tradeable") is not True or not timedelta(seconds=-180) <= now-snapshot <= timedelta(minutes=20):
        return None, "STALE_OR_UNTRADEABLE_OANDA_QUOTE"
    bid, ask, pip, spread_pips = [number(q.get(k)) for k in ("bid", "ask", "pip_size", "spread_pips")]
    if None in (bid, ask, pip, spread_pips) or not (0 < bid < ask and pip > 0 and 0 < spread_pips <= 10):
        return None, "INVALID_BID_ASK_OR_SPREAD"
    indicators = {}
    for tf in (("M15", "H1") if paper_only else ("M15", "H1", "H4", "D")):
        item = (frames.get(tf) or {}).get("indicators") or {}
        if trend(item) is None:
            return None, "REQUIRED_FRAME_HISTORY_MISSING:" + tf
        indicators[tf] = item
    m, h = indicators["M15"], indicators["H1"]
    if trend(m) != direction or trend(h) != direction:
        return None, "FOUR_FRAME_TREND_CONFLICT"
    daily_conflict = (not paper_only and trend(indicators["D"]) != direction)
    # Three-frame momentum exceptions require independent trend strength,
    # directional DMI and aligned MACD; neither waives cost or news checks.
    strong_core = (
        route == "MOMENTUM_CONTINUATION"
        and all((number(i.get("adx14")) or 0) >= 25 for i in (m, h))
        and all(
            number(i.get("plus_di14")) is not None
            and number(i.get("minus_di14")) is not None
            and (number(i["plus_di14"]) - number(i["minus_di14"])) *
                (1 if direction == "LONG" else -1) >= 8
            for i in (m, h))
        and (number((m.get("macd_12_26_9") or {}).get("histogram")) or 0) *
            (1 if direction == "LONG" else -1) > 0
    )
    # An opposing H4 is permissible only when its ADX shows a weak
    # trend, the daily trend supports direction and M15/H1 are strong.
    weak_h4_exception = (
        not paper_only and strong_core and not daily_conflict
        and trend(indicators["H4"]) != direction
        and 0 < (number(indicators["H4"].get("adx14")) or 0) < 18
    )
    # The *only* relaxed higher-frame case is a continuation with M15/H1/H4
    # aligned, D opposed, independently strong momentum, and intact risk/news.
    # Two opposed/missing higher frames remain disqualifying.
    strong_three_frame = (
        daily_conflict and route == "MOMENTUM_CONTINUATION"
        and trend(indicators["H4"]) == direction
        and strong_core
    )
    if (not paper_only and trend(indicators["H4"]) != direction
            and not weak_h4_exception) or (
            daily_conflict and not strong_three_frame):
        return None, "FOUR_FRAME_TREND_CONFLICT"
    # Moderately extended plans are not permission to chase; later watcher
    # must separately check live risk economics and a NEW completed M15 bar.
    if moderate_extension and not (strong_core and
            (strong_three_frame or (not paper_only and not daily_conflict))):
        return None, "REVERSAL_OR_EXHAUSTION"
    atr = number(m.get("atr14"))
    if not atr or atr <= 0 or spread_pips * pip > .50 * atr:
        return None, "SPREAD_TOO_HIGH_RELATIVE_TO_VOLATILITY"
    if any((number(i.get("adx14")) or 0) < (18 if paper_only else 20)
           for i in ((h,) if paper_only else (m, h))):
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
        # Champion continuation is now observed by a *future* completed M5
        # candle; admission must have real, fresh M5 data available.
        if not paper_only:
            f5 = (frames.get("M5") or {})
            m5_bars = f5.get("recent_completed_candles") or f5.get("last_3_completed_candles") or []
            m5_open = moment(m5_bars[-1].get("time")) if len(m5_bars) >= 2 else None
            if (m5_open is None or m5_open + timedelta(minutes=5) > now
                    or now - (m5_open + timedelta(minutes=5)) > timedelta(minutes=15)):
                return None, "RECENT_COMPLETED_M5_MISSING"
        span = high - low
        body = abs(close - opening)
        close_strength = ((close - low) / span if direction == "LONG" else
                          (high - close) / span) if span > 0 else 0
        # Only qualifying champion momentum plans use the slightly earlier
        # 48% / 68% impulse; their frozen thresholds follow into the watcher.
        minimum_body = .48 if not paper_only else .55
        minimum_close = .68 if not paper_only else .72
        if span <= 0 or body / span < minimum_body or close_strength < minimum_close:
            return None, "NO_DECISIVE_COMPLETED_M15_IMPULSE"
        if not (close > opening if direction == "LONG" else close < opening):
            return None, "IMPULSE_DIRECTION_CONFLICT"
        if abs(live_side - close) > (.55 if not paper_only else .40) * atr:
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
    target_indicators = [(frames.get(tf) or {}).get("indicators") or {}
                         for tf in ("M15", "H1", "H4", "D")]
    if direction == "LONG":
        stop = min(swing_low - .12 * atr, entry - 1.05 * atr)
        potential = sorted({float(i["resistance20"]) for i in target_indicators
                            if number(i.get("resistance20")) is not None
                            and number(i.get("resistance20")) > entry})
    else:
        stop = max(swing_high + .12 * atr, entry + 1.05 * atr)
        potential = sorted({float(i["support20"]) for i in target_indicators
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
    # fast/hourly watcher verifies actual later completed M5 bars for new
    # champion continuations, with the original completed M15 impulse fixed.
    decimals = 3 if pip >= .01 else 5
    now_utc = now.isoformat().replace("+00:00", "Z")
    deadline = (now + timedelta(minutes=90)).isoformat().replace("+00:00", "Z")
    identifier = "AUTO-" + now.strftime("%Y%m%dT%H%M") + "-" + pair + "-" + (
        "L" if direction == "LONG" else "S") + ("-C" if route == "MOMENTUM_CONTINUATION" else "-B")
    # Freeze all KNOWN relevant high-impact windows overlapping plan life,
    # including windows that start after today's admission check. Replay and
    # watcher must share the same future-event restrictions.
    windows = []
    for event in (news.get("calendar") or {}).get("events") or []:
        at = moment(event.get("time"))
        if (at and event.get("currency") in pair.split("_")
                and "high" in str(event.get("impact", "")).lower()):
            start, end = at - timedelta(minutes=45), at + timedelta(minutes=45)
            if end >= now and start <= now + timedelta(minutes=90):
                windows.append({"start": start.isoformat(), "end": end.isoformat(),
                                "title": event.get("title"), "currency": event.get("currency")})
    plan = {
        "id": identifier, "instrument": pair, "direction": direction,
        "created_at": now_utc, "expires_at": deadline,
        "planned_entry": round(entry, decimals), "stop": round(stop, decimals),
        "pip_size": pip,
        "tp1": round(tp, decimals),
        "max_oanda_spread_pips": round(spread_cap, 3),
        "event_blackouts_utc": windows, "entry_route": route,
        "oanda_snapshot_at": now_utc,
        "source_quote_at": q.get("time"),
        "research_only": True, "broker_verified": False,
        "paper_only": paper_only, "admission_policy": policy,
        "account_equity_verified": False,
        "max_equity_risk_fraction": .0025,
        "manual_broker_check_required": True,
        "gross_rr_at_creation": round(gross, 3),
        "net_rr_at_spread_cap": round(net, 3),
        "technical_context": {
            "full_frame_alignment": not paper_only and not daily_conflict,
            "timeframes_aligned": [tf for tf in ("M15", "H1", "H4", "D")
                                   if trend((frames.get(tf) or {}).get("indicators") or {}) == direction],
            "daily_frame_conflict": daily_conflict,
            "conditional_daily_exception": strong_three_frame,
            "conditional_weak_h4_exception": weak_h4_exception,
            "extension_exception": moderate_extension,
            "extension_cap_atr": 2.0 if not paper_only else 1.85,
            "continuation_min_body": (.50 if not paper_only else .55),
            "continuation_min_close": (.70 if not paper_only else .72),
            "admission_policy": policy,
            "momentum_adx_minimum": 18 if paper_only else 20,
            "m15_adx": number(m.get("adx14")), "h1_adx": number(h.get("adx14")),
            "m15_plus_di": number(m.get("plus_di14")),
            "m15_minus_di": number(m.get("minus_di14")),
            "h1_plus_di": number(h.get("plus_di14")),
            "h1_minus_di": number(h.get("minus_di14")),
            "m15_atr": atr,
            "source_bar_time": bars[-1].get("time"),
        },
        "target_basis": "nearest completed M15/H1/H4/D 20-bar support/resistance",
        "trigger_timeframe": ("M5" if route == "MOMENTUM_CONTINUATION"
                              and not paper_only else "M15"),
        "trigger_basis": ("new completed M5 breakout past frozen completed M15 impulse"
                          if route == "MOMENTUM_CONTINUATION" and not paper_only
                          else "future completed M15 only"),
        "decision_status": ("CONDITIONAL_PENDING_NEW_M5_TRIGGER"
                             if route == "MOMENTUM_CONTINUATION" and not paper_only
                             else "CONDITIONAL_PENDING_NEW_M15_TRIGGER"),
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

def decision_now():
    return datetime.now(timezone.utc)

def admit(summary, news, previous, archive=None, *, evaluated_at=None):
    source_time = moment(summary.get("collected_at"))
    now = moment(evaluated_at) if evaluated_at is not None else source_time
    audit = {"source_at": summary.get("collected_at"),
             "evaluated_at": now.isoformat() if now else None, "admitted": [],
             "rejected": {}, "candidate_routes": [],
             "active": 0, "status": "NO_SOURCE"}
    if not now or not source_time or now < source_time or summary.get("status") not in ("OK", "PARTIAL"):
        return previous, archive or {"signals": []}, audit
    previous = previous if isinstance(previous, dict) else {"schema_version": 1, "signals": []}
    historic = deepcopy(archive) if isinstance(archive, dict) else {"signals": []}
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
            audit["candidate_routes"].append({"pair": pair, "route": route,
                "direction": candidate.get("deep_entry_scout_direction") or candidate.get("potential_direction_not_approved"),
                "status": "REJECTED" if error else "QUALIFIED_CONDITIONAL_MANUAL_SIGNAL",
                "reason": error})
            if error:
                audit["rejected"].setdefault(error, 0)
                audit["rejected"][error] += 1
                continue
            if len(kept) >= MAX_PLANS:
                break
            proposal["oanda_snapshot_at"] = summary["collected_at"]
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
    updated, archived, audit = admit(summary, news, previous, archive,
                                    evaluated_at=decision_now().isoformat())
    (ROOT / "data/signal_admission_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n")
    if updated != previous:  # Refresh empty-plan timestamp as well; never rewrite individual plan fields.
        path.write_text(json.dumps(updated, indent=2) + "\n")
    if archived.get("signals") != archive.get("signals"):
        archive_path.write_text(json.dumps(archived, indent=2) + "\n")
    print("Conditional FX admission", audit["status"], "active", audit["active"],
          "new", len(audit["admitted"]), "rejected", audit["rejected"])

if __name__ == "__main__":
    main()

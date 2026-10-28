"""Fail-closed, research-only entry diagnostics for FX desk.

This is a *diagnostic* of the next completed-candle level, NOT a signal
generator, order route, broker price, or assertion of statistical edge.
No direction/price can be upgraded into signals.json by this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite


def _float(v):
    try:
        x = float(v)
        return x if isfinite(x) else None
    except (ValueError, TypeError):
        return None


def _time(v):
    try:
        if not isinstance(v, str):
            return None
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _dir(indicators):
    a = _float(indicators.get("ema20"))
    b = _float(indicators.get("ema50"))
    if a is None or b is None:
        return "UNKNOWN"
    return "LONG" if a > b else "SHORT" if a < b else "NEUTRAL"


def _reason(code, detail):
    return {"code": code, "detail": detail}


def _priced_breakout_research(direction, level, prev, last, indicators, atr, spread_pips, pip, now, next_bar):
    """Only a prospective structural M15 paper plan, never a canonical signal.

    Entry requires a FUTURE completed candle after this snapshot, followed by
    a new risk/spread/news review. No retroactive fills and no fixed win odds.
    """
    if not (direction in ("LONG", "SHORT") and level and atr and atr > 0
            and pip and pip > 0 and spread_pips and spread_pips > 0
            and isinstance(prev, dict) and isinstance(last, dict)
            and next_bar is not None):
        return None, "Insufficient completed structure, ATR or spread"
    old, new = prev.get("mid") or {}, last.get("mid") or {}
    highs = [_float(old.get("h")), _float(new.get("h"))]
    lows = [_float(old.get("l")), _float(new.get("l"))]
    target = _float(indicators.get("resistance20" if direction == "LONG" else "support20"))
    if any(x is None for x in highs + lows) or target is None:
        return None, "No independent M15 swing/target structure to justify a target"
    spread = spread_pips * pip
    buffer = max(spread * 1.15, atr * 0.07)
    if buffer >= atr * .23:
        return None, "Spread is too large for a non-chasing M15 reference entry zone"
    if direction == "LONG":
        low, high = level + buffer, level + atr * .23
        stop = min(lows) - .12 * atr
        worst_entry = high
        risk, reward = worst_entry - stop, target - worst_entry
    else:
        low, high = level - atr * .23, level - buffer
        stop = max(highs) + .12 * atr
        worst_entry = low
        risk, reward = stop - worst_entry, worst_entry - target
    if risk <= 0 or reward <= 0 or not low < high:
        return None, "Next structural stop or target is on the wrong side of entry"
    gross = reward / risk
    net = (reward - spread) / (risk + spread)
    if gross < 1.50 or net < 1.35:
        return None, "Nearest M15 structural target fails the existing spread-adjusted reward threshold"
    decimals = 3 if pip >= .01 else 5
    return {
        "status": "PRICED_PAPER_RESEARCH_NOT_TRIGGERED",
        "approved_entry": False, "broker_verified": False,
        "entry_zone_low": round(low, decimals),
        "entry_zone_high": round(high, decimals),
        "stop": round(stop, decimals), "tp1": round(target, decimals),
        "structural_target": "M15 resistance20" if direction == "LONG" else "M15 support20",
        "gross_rr_estimate": round(gross, 2),
        "net_rr_estimate": round(net, 2),
        "oanda_spread_pips": round(spread_pips, 2),
        "reference_level": round(level, decimals),
        "requires_future_completed_m15": True,
        "next_m15_check_at": next_bar.isoformat().replace("+00:00", "Z"),
        "expires_at": (next_bar + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
        "source_snapshot_at": now.isoformat().replace("+00:00", "Z"),
        "disclaimer": "PAPER-ONLY structural feasibility; no verified entry, fill, cost clearance or proven edge.",
    }, None


def entry_diagnostics(summary: dict, news: dict | None = None, limit: int = 8):
    """Inspect top current candidates + core majors using only completed bars.

    Preserve source ranking as a RESEARCH priority, not an approval score.
    Outbound fields deliberately exclude broker/account or client identifiers.
    """
    news = news or {}
    snapshot = _time(summary.get("collected_at"))
    if snapshot is None:
        return []
    pairs = summary.get("pairs") or {}
    ranks = summary.get("ranked_review_candidates") or []
    by_key = {x.get("instrument"): x for x in ranks if isinstance(x, dict)}
    order = [x.get("instrument") for x in ranks if isinstance(x, dict)]
    for major in ("GBP_USD", "EUR_USD", "USD_JPY"):
        if major in pairs and major not in order:
            order.append(major)
    # Audit the *entire* ranked/quoted universe before selecting the compact
    # institutional desk. Previously only the first five plus three majors
    # were assessed, causing technically closer-to-entry pairs to be skipped.
    # Missing higher-frame data still fails closed; this changes research
    # selection, not canonical signal admission or broker execution.
    core = tuple(m for m in ("GBP_USD", "EUR_USD", "USD_JPY") if m in pairs)
    selected = list(dict.fromkeys(order + list(core)))
    results = []
    for instrument in selected:
        if instrument == "GBP_ZAR":
            continue
        source = pairs.get(instrument) or {}
        quote, frames = source.get("quote") or {}, source.get("frames") or {}
        ranked = by_key.get(instrument) or {}
        direction = ranked.get("potential_direction_not_approved")
        if direction not in ("LONG", "SHORT"):
            continue
        qtime = _time(quote.get("time"))
        m15 = frames.get("M15") or {}
        bar_list = m15.get("last_3_completed_candles") or []
        reasons = []
        pip = _float(quote.get("pip_size"))
        bid, ask = _float(quote.get("bid")), _float(quote.get("ask"))
        atr = _float((m15.get("indicators") or {}).get("atr14"))
        spread = _float(quote.get("spread_pips"))
        if qtime is None or not timedelta(seconds=-300) <= snapshot-qtime < timedelta(minutes=70):
            reasons.append(_reason("STALE_QUOTE", "OANDA pair quote is missing or outside the 70-minute limit"))
        if quote.get("tradeable") is not True or not (bid and ask and ask > bid):
            reasons.append(_reason("QUOTE_UNUSABLE", "No valid OANDA two-sided tradable research quote"))
        if not pip or not atr or not spread or spread <= 0 or spread*pip > .5*atr:
            reasons.append(_reason("COST_HIGH_OR_UNKNOWN", "Spread exceeds half M15 ATR or cost inputs unavailable"))
        frames_seen = {}
        for tf in ("M15", "H1", "H4", "D"):
            frames_seen[tf] = _dir((frames.get(tf) or {}).get("indicators") or {})
        agreed = sum(value == direction for value in frames_seen.values())
        if frames_seen["M15"] != direction or frames_seen["H1"] != direction:
            reasons.append(_reason("FOUNDATION_CONFLICT", "M15/H1 EMA trends do not align with the research direction"))
        missing_frames = [tf for tf, state in frames_seen.items() if state == "UNKNOWN"]
        if missing_frames:
            reasons.append(_reason(
                "HIGHER_FRAME_MISSING",
                "Not audited on " + ", ".join(missing_frames) +
                "; missing history is NOT evidence of an opposing trend"))
        elif agreed < 4:
            reasons.append(_reason("HIGHER_FRAME_CONFLICT", str(agreed)+"/4 full-frame alignment (M15, H1, H4, D)"))
        m15_i = m15.get("indicators") or {}
        h1_i = (frames.get("H1") or {}).get("indicators") or {}
        ma, ha = _float(m15_i.get("adx14")), _float(h1_i.get("adx14"))
        if ma is None or ha is None or ma < 20 or ha < 20:
            reasons.append(_reason("TREND_STRENGTH", "M15 ADX "+(f"{ma:.1f}" if ma is not None else "missing")+
                                   ", H1 ADX "+(f"{ha:.1f}" if ha is not None else "missing")+"; research threshold 20"))
        if ma is not None and ha is not None:
            plus, minus = _float(m15_i.get("plus_di14")), _float(m15_i.get("minus_di14"))
            hp, hm = _float(h1_i.get("plus_di14")), _float(h1_i.get("minus_di14"))
            if any(x is None for x in (plus, minus, hp, hm)) or not (
                (minus > plus and hm > hp) if direction == "SHORT" else
                (plus > minus and hp > hm)):
                reasons.append(_reason("DMI_CONFLICT", "M15/H1 directional movement not jointly aligned"))
        adaptive = ranked.get("adaptive_context") or {}
        if adaptive.get("block_new_alert") is True:
            reasons.append(_reason("OVEREXTENDED_OR_REVERSING", str(adaptive.get("reason") or "Adaptive reversal warning")))
        complete_at, next_check_at, level, observed = None, None, None, False
        if len(bar_list) >= 2:
            prev, last = bar_list[-2:]
            opened = _time(last.get("time"))
            before = prev.get("mid") or {}
            last_mid = last.get("mid") or {}
            low, high = _float(before.get("l")), _float(before.get("h"))
            o, c = _float(last_mid.get("o")), _float(last_mid.get("c"))
            if opened:
                completed = opened + timedelta(minutes=15)
                if completed <= snapshot and snapshot-completed < timedelta(minutes=45):
                    complete_at = completed.isoformat().replace("+00:00", "Z")
                    next_check_at = (completed + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
                    level = low if direction == "SHORT" else high
                    if level is not None and o is not None and c is not None:
                        observed = (c < o and c < level) if direction == "SHORT" else (c > o and c > level)
                else:
                    reasons.append(_reason("M15_BAR_STALE", "Latest available completed M15 bar is not timely"))
            else:
                reasons.append(_reason("M15_BAR_TIME", "Missing completed M15 timestamp"))
        else:
            reasons.append(_reason("M15_HISTORY", "Two completed M15 candles unavailable for a non-lookahead level"))
        if level is None:
            reasons.append(_reason("PRICE_LEVEL_UNAVAILABLE", "No legitimate prior M15 candle level"))
        else:
            reasons.append(_reason("NO_APPROVED_TRIGGER", "A completed move beyond this reference alone is NOT a canonical entry"))
        # Compute fully priced structural feasibility only when the actual
        # trend, spread and candle evidence have passed. This is a challenger
        # PAPER hypothesis, never promotion into signals.json.
        research_preview = None
        hard_codes = {"STALE_QUOTE", "QUOTE_UNUSABLE", "COST_HIGH_OR_UNKNOWN",
                      "FOUNDATION_CONFLICT", "HIGHER_FRAME_CONFLICT", "HIGHER_FRAME_MISSING",
                      "TREND_STRENGTH", "DMI_CONFLICT",
                      "OVEREXTENDED_OR_REVERSING", "M15_BAR_STALE",
                      "M15_BAR_TIME", "M15_HISTORY", "PRICE_LEVEL_UNAVAILABLE"}
        if level is not None and next_check_at and not any(
                item["code"] in hard_codes for item in reasons):
            next_time = _time(next_check_at)
            research_preview, preview_reason = _priced_breakout_research(
                direction, level, prev, last, m15_i, atr, spread, pip,
                snapshot, next_time)
            if preview_reason:
                reasons.append(_reason("STRUCTURAL_REWARD", preview_reason))
        # Global PARTIAL frequently reflects unrelated optional feeds (for
        # example, US data sites while analysing EUR/AUD). Evaluate the pair's
        # mandatory calendar/official sources and material headlines separately.
        # LIMITED never means a universal news all-clear, and neither this
        # diagnostic nor a clean pair check approves a trade.
        pair_news = (news.get("by_pair") or {}).get(instrument)
        if not isinstance(pair_news, dict):
            if news.get("status") != "OK":
                reasons.append(_reason("NEWS_PARTIAL",
                                       "No independently verified pair news clearance; global coverage partial"))
            else:
                reasons.append(_reason("NEWS_PAIR_NOT_CHECKED",
                                       "Pair-specific calendar and official-source checks unavailable"))
        else:
            coverage = pair_news.get("coverage") or {}
            if pair_news.get("calendar_coverage") != "OK":
                reasons.append(_reason("NEWS_CALENDAR_MISSING",
                                       "Pair-specific economic calendar not independently checked"))
            if coverage.get("missing_required"):
                reasons.append(_reason("NEWS_REQUIRED_SOURCE_MISSING",
                                       "Required official/market sources missing for this pair"))
            if pair_news.get("near_high_impact_calendar"):
                reasons.append(_reason("NEWS_EVENT_BLACKOUT",
                                       "High-impact release near this pair: fresh manual event check required"))
            if pair_news.get("recent_relevant_headlines"):
                reasons.append(_reason("NEWS_MATERIAL_REVIEW",
                                       "Recent relevant market headlines require individual review"))
            if coverage.get("status") == "LIMITED":
                reasons.append(_reason("NEWS_OPTIONAL_GAPS",
                                       "Pair's required feeds checked but optional sources missing; not a news all-clear"))
            elif coverage.get("status") != "OK":
                reasons.append(_reason("NEWS_REQUIRED_SOURCE_MISSING",
                                       "Pair news coverage incomplete or unverified"))
        if qtime is not None and bid is not None and ask is not None and pip:
            distance = ((ask-level) if direction == "SHORT" else (level-bid))/pip if level else None
        else:
            distance = None
        results.append({
            "instrument": instrument, "direction": direction,
            "source_quote_at": quote.get("time"),
            "priority": round(float(ranked.get("research_priority") or 0), 2),
            "status": "DIRECTIONAL_RESEARCH_ONLY_NOT_AN_ENTRY",
            "broker_verified": False, "approved_entry": False,
            "frame_alignment": f"{agreed}/4",
            "m15_adx": round(ma, 1) if ma is not None else None,
            "h1_adx": round(ha, 1) if ha is not None else None,
            "reference_m15_level": round(level, 3 if pip and pip>=.01 else 5) if level is not None else None,
            "reference_rule": ("NEXT COMPLETED M15 CLOSE BELOW PRIOR LOW" if direction=="SHORT"
                               else "NEXT COMPLETED M15 CLOSE ABOVE PRIOR HIGH"),
            "last_m15_completed_at": complete_at,
            "next_m15_check_at": next_check_at,
            "previous_m15_break_observed": observed,
            "distance_to_reference_pips": round(distance, 1) if distance is not None else None,
            "priced_paper_preview": research_preview,
            "blockers": reasons[:12],  # Keep safety/news event blockers visible after technical gates.
        })
    # Choose which *research diagnostics* to display from all evaluated
    # instruments. Do not equate closest to reference with a valid entry.
    # Hard risk/technical blockers outrank heuristic priority, while the
    # original top-three majors retain explicit exception visibility.
    hard = {
        "STALE_QUOTE", "QUOTE_UNUSABLE", "COST_HIGH_OR_UNKNOWN",
        "FOUNDATION_CONFLICT", "HIGHER_FRAME_CONFLICT", "HIGHER_FRAME_MISSING", "TREND_STRENGTH",
        "DMI_CONFLICT", "OVEREXTENDED_OR_REVERSING", "M15_BAR_STALE",
        "M15_BAR_TIME", "M15_HISTORY", "PRICE_LEVEL_UNAVAILABLE",
    }

    def research_rank(row):
        blockers = {item["code"] for item in row["blockers"]}
        distance = _float(row.get("distance_to_reference_pips"))
        return (
            sum(code in blockers for code in hard),
            0 if row.get("frame_alignment") == "4/4" else 1,
            abs(distance) if distance is not None else float("inf"),
            -row.get("priority", 0),
        )

    sorted_rows = sorted(results, key=research_rank)
    core_rows = [next((r for r in results if r["instrument"] == symbol), None)
                 for symbol in core]
    core_rows = [r for r in core_rows if r is not None]
    remaining = max(0, limit - len(core_rows))
    top_non_core = [r for r in sorted_rows if r["instrument"] not in core][:remaining]
    return sorted(top_non_core + core_rows, key=research_rank)[:limit]

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
        if agreed < 4:
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
        if news.get("status") != "OK":
            reasons.append(_reason("NEWS_PARTIAL", "Macro/news sources are not independently cleared"))
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
            "blockers": reasons[:8],
        })
    # Choose which *research diagnostics* to display from all evaluated
    # instruments. Do not equate closest to reference with a valid entry.
    # Hard risk/technical blockers outrank heuristic priority, while the
    # original top-three majors retain explicit exception visibility.
    hard = {
        "STALE_QUOTE", "QUOTE_UNUSABLE", "COST_HIGH_OR_UNKNOWN",
        "FOUNDATION_CONFLICT", "HIGHER_FRAME_CONFLICT", "TREND_STRENGTH",
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

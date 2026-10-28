"""Prospective FX technical recheck and afternoon challenger audit.

Research only. Observed directional movement is not a trade return. This module
never changes immutable plans, broker orders, or champion cost/risk thresholds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math

LONDON = ZoneInfo("Europe/London")


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except ValueError:
        return None


def entry_context(pairdata, direction):
    """Check the *current* M15/H1 state before accepting any new price alert.

    No vote counting or inferred win probability. A reversal requires at
    least two independent manifestations (DMI + MACD or completed bodies);
    extension is price distance from M15 EMA20 measured in M15 ATR units.
    Missing indicators mean the recheck has not been independently performed.
    """
    output = {"status": "UNVERIFIABLE", "block_new_alert": True,
              "reason": "Completed M15/H1 evidence is unavailable.",
              "m15_ema_distance_atr": None}
    if direction not in ("LONG", "SHORT") or not isinstance(pairdata, dict):
        return output
    quote = pairdata.get("quote") or {}
    frames = pairdata.get("frames") or {}
    m15 = (frames.get("M15") or {}).get("indicators") or {}
    h1 = (frames.get("H1") or {}).get("indicators") or {}
    sign = 1 if direction == "LONG" else -1
    bid, ask = _number(quote.get("bid")), _number(quote.get("ask"))
    ema, atr = _number(m15.get("ema20")), _number(m15.get("atr14"))
    plus, minus = _number(m15.get("plus_di14")), _number(m15.get("minus_di14"))
    histogram = _number((m15.get("macd_12_26_9") or {}).get("histogram"))
    hplus, hminus = _number(h1.get("plus_di14")), _number(h1.get("minus_di14"))
    hadx = _number(h1.get("adx14"))
    if None in (bid, ask, ema, atr, plus, minus, histogram, hplus, hminus, hadx):
        return output
    if not (0 < bid < ask and atr > 0):
        return output
    distance = sign * (((bid + ask) / 2) - ema) / atr
    output["m15_ema_distance_atr"] = round(distance, 3)
    m15_opposed = sign * (plus - minus) < 0
    macd_opposed = sign * histogram < 0
    h1_opposed = sign * (hplus - hminus) < 0 and hadx >= 22

    candles = ((frames.get("M15") or {}).get("recent_completed_candles")
               or (frames.get("M15") or {}).get("last_3_completed_candles") or [])
    # Only use documented completed candles; no current unfinished M15 candle.
    bodies_opposed = False
    if len(candles) >= 2:
        last_two = candles[-2:]
        try:
            bodies_opposed = all(
                sign * (float(c["mid"]["c"]) - float(c["mid"]["o"])) < 0
                for c in last_two)
        except (KeyError, TypeError, ValueError):
            bodies_opposed = False

    if (m15_opposed and macd_opposed) or (m15_opposed and bodies_opposed) or (
            m15_opposed and h1_opposed):
        output.update(status="REASSESS_REVERSAL", block_new_alert=True,
                      reason="M15 momentum has turned against this direction; recheck the H1 trend before a new entry.")
    elif distance > 1.55:
        output.update(status="REASSESS_EXTENDED", block_new_alert=True,
                      reason="Price is over 1.55 M15 volatility units beyond EMA20; avoid chasing the move.")
    elif m15_opposed or macd_opposed or h1_opposed:
        output.update(status="CAUTION_MIXED_MOMENTUM", block_new_alert=False,
                      reason="Momentum is mixed; require the original completed-candle trigger, not directional bias alone.")
    else:
        output.update(status="NO_NEW_TECHNICAL_WARNING", block_new_alert=False,
                      reason="No reversal or overextension warning on the available completed M15/H1 evidence.")
    return output


def afternoon_audit(book, checked_at):
    """Audit *every* candidate first registered 15:00-23:00 London time.

    Baselines are frozen by the paper ledger. Outcomes remain unscored unless
    independent prospective bid/ask candle replay confirms a valid entry/exit.
    Keep the latest available afternoon cohort when reviewing overnight.
    """
    now = _date(checked_at) if isinstance(checked_at, str) else checked_at
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Audit time must have a timezone")
    now = now.astimezone(timezone.utc)
    candidates = (book or {}).get("candidates") or {}
    selected = []
    for key, row in candidates.items():
        if not isinstance(row, dict):
            continue
        created = _date((row.get("plan") or {}).get("created_at"))
        if not created or created > now:
            continue
        local = created.astimezone(LONDON)
        if not 15 <= local.hour < 23:
            continue
        selected.append((key, row, local.date().isoformat(), created))
    session = max((item[2] for item in selected), default=None)
    selected = [item for item in selected if item[2] == session]
    rows = []
    counts = {"adverse": 0, "favourable": 0, "flat": 0,
              "insufficient": 0, "missing": 0}
    for key, row, day, created in sorted(selected):
        tracking = row.get("tracking") or {}
        history = sorted(
            (h for h in (tracking.get("history") or [])
             if isinstance(h, dict) and _date(h.get("at"))
             and _date(h["at"]) >= created
             and _date(h["at"]).astimezone(LONDON).date().isoformat() == day),
            key=lambda h: h["at"])
        latest = history[-1] if history else None
        move = _number(latest.get("move_pct")) if latest else None
        # First frozen observation is not evidence of subsequent performance.
        if len(history) < 2 or move is None:
            classification = "INSUFFICIENT_HISTORY" if history else "MISSING_OBSERVATIONS"
            counts["insufficient" if history else "missing"] += 1
        elif move < -0.00001:
            classification = "ADVERSE_DIRECTIONAL_MOVEMENT"
            counts["adverse"] += 1
        elif move > 0.00001:
            classification = "FAVOURABLE_DIRECTIONAL_MOVEMENT"
            counts["favourable"] += 1
        else:
            classification = "FLAT"
            counts["flat"] += 1
        plan = row.get("plan") or {}
        rows.append({
            "id": key, "pair": plan.get("instrument"),
            "direction": plan.get("direction"),
            "created_at": plan.get("created_at"),
            "first_observed_at": history[0].get("at") if history else None,
            "last_observed_at": latest.get("at") if latest else None,
            "observations": len(history),
            "directional_move_pct_from_frozen_reference": round(move, 5) if move is not None else None,
            "reference_type": tracking.get("reference_kind"),
            "paper_status": (row.get("result") or {}).get("status"),
            "classification": classification,
        })
    return {
        "session_date_uk": session, "checked_at": now.isoformat(),
        "registered": len(rows), **counts, "candidates": rows,
        "challenger": "PAPER_ONLY_NOT_VALIDATED",
        "rule_under_test": "M15 DMI/MACD reversal and >1.55 ATR EMA20 extension",
        "sample_warning": "Directional movements are NOT filled trades, realised losses, success probabilities or evidence sufficient to change champion parameters.",
    }

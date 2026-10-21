"""FX instrument discovery and objective screening — research only.

The Pepperstone UK website advertises 90+ FX pairs but does not expose a
stable, authenticated client-specific symbol API here. Discover every
standard OANDA account CURRENCY instrument and explicitly mark Pepperstone
availability as UNVERIFIED unless individually checked with Pepperstone.
An OANDA quote alone NEVER establishes that the pair is offered as a UK
Pepperstone spread bet. NDFs, currency indices and inverse synthetic pairs
are NOT fabricated from spot quotations.
"""
import math
import re

CORE = ("EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD",
        "EUR_GBP", "AUD_USD", "USD_CHF", "EUR_JPY")
MAJOR = frozenset(("EUR", "USD", "GBP", "AUD", "CAD", "CHF", "NZD", "JPY"))
# Specific pairs named in Pepperstone UK public educational/markets material,
# not a complete or account-confirmed list of UK spread-betting instruments.
PEPPERSTONE_UK_PUBLIC_EXAMPLES = frozenset((
    "AUD_USD", "EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD",
    "USD_CHF", "EUR_GBP", "AUD_NZD", "AUD_CHF", "GBP_AUD",
))
EXPLICIT_PEPPERSTONE_NDF_EXAMPLES = frozenset(("USD_BRL", "USD_INR"))
NAME = re.compile(r"^[A-Z]{3}_[A-Z]{3}$")


def is_fx(instrument):
    name = instrument.get("name", "")
    return (instrument.get("type") == "CURRENCY" and NAME.fullmatch(name)
            and name.split("_")[0] != name.split("_")[1])


def catalog(instruments):
    found = {}
    for instrument in instruments:
        if not isinstance(instrument, dict) or not is_fx(instrument):
            continue
        name = instrument["name"]
        pip_location = instrument.get("pipLocation")
        try:
            pip = 10 ** int(pip_location)
            if not 0 < pip <= 1:
                raise ValueError("invalid pip")
        except (TypeError, ValueError, OverflowError):
            continue
        found[name] = {
            "instrument": name,
            "display_name": instrument.get("displayName", name.replace("_", "/")),
            "pip_location": int(pip_location), "pip_size": pip,
            "oanda_type": "CURRENCY",
            "pepperstone_uk_publicly_referenced": name in PEPPERSTONE_UK_PUBLIC_EXAMPLES,
            "pepperstone_spreadbet_account_availability": "UNVERIFIED",
            "research_universe_note": ("Publicly referenced by Pepperstone UK; "
                                       "specific account availability still unverified"
                                       if name in PEPPERSTONE_UK_PUBLIC_EXAMPLES
                                       else "OANDA account currency pair; Pepperstone match unverified"),
        }
    return dict(sorted(found.items()))


def chunks(seq, size):
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def is_quote_usable(quote):
    try:
        return (quote.get("tradeable") is True and
                math.isfinite(float(quote["bid"])) and
                math.isfinite(float(quote["ask"])) and
                0 < float(quote["bid"]) < float(quote["ask"]) and
                math.isfinite(float(quote["spread_pips"])) and
                0 < float(quote["spread_pips"]) < 100000)
    except (ValueError, TypeError, KeyError):
        return False


def screen_priority(quote, m15, h1):
    """Cost-aware research triage across every quoted instrument, never a trade signal.

    Trade admission applies separate prospective entry/stop/TP and broker-spread
    rules. Spread relative to a *single M15 ATR* is only a screening variable,
    NOT a hard reason to exclude a pair from chart analysis.
    """
    if not is_quote_usable(quote):
        return None
    m, h = m15["indicators"], h1["indicators"]
    atr = m.get("atr14") or 0
    pip = quote["pip_size"]
    spread = quote["spread_pips"] * pip
    if atr <= 0 or spread <= 0:
        return None
    cost_fraction = spread / atr
    adx15, adx1 = m.get("adx14") or 0, h.get("adx14") or 0
    rsi = m.get("rsi14")
    mid = (quote["bid"] + quote["ask"]) / 2
    ema15, ema1 = m.get("ema20"), h.get("ema20")
    if rsi is None or ema15 is None or ema1 is None:
        return None
    same_side = (mid - ema15) * (mid - ema1) > 0
    # Assess opposing regimes without conflating a strong trend with
    # automatically attractive entries: a trend can be overextended.
    trending = min(35.0, (adx15 + adx1) / 2)
    ranging = max(0.0, 24.0 - (adx15 + adx1) / 2)
    regime_score = max(trending, ranging * 0.75)
    # Relative spread is heavily penalised but NEVER removes chart coverage.
    spread_penalty = min(75.0, cost_fraction * 12.0)
    stretched_penalty = max(0.0, abs(rsi - 50) - 24.0) * 0.4
    # Ranked technical evidence spans ten selected METHODS rather than 100
    # correlated votes. None is an independently proven predictive edge.
    m_adv = m15.get("advanced") or {}
    h_adv = h1.get("advanced") or {}
    direction = "LONG" if mid >= ema1 else "SHORT"
    sign = 1 if direction == "LONG" else -1
    h_cloud = (h_adv.get("ichimoku_9_26_52") or {}).get("price_vs_visible_cloud")
    h_supertrend = (h_adv.get("supertrend_10_3") or {}).get("direction")
    m_breakout = (m_adv.get("donchian_20") or {}).get("close_breakout")
    macd_hist = (m.get("macd_12_26_9") or {}).get("histogram")
    direction_ok = {
        "ema_stack": bool(m.get("ema50") is not None and
                          h.get("ema50") is not None and
                          sign * (m["ema20"] - m["ema50"]) > 0 and
                          sign * (h["ema20"] - h["ema50"]) > 0),
        "adx_di": bool(adx15 >= 18 and adx1 >= 18 and
                       sign * ((m.get("plus_di14") or 0) -
                               (m.get("minus_di14") or 0)) > 0),
        "macd": bool(macd_hist is not None and sign * macd_hist > 0),
        "supertrend": h_supertrend == ("UP" if sign == 1 else "DOWN"),
        "ichimoku": h_cloud == ("ABOVE" if sign == 1 else "BELOW"),
        "donchian": m_breakout == ("UP" if sign == 1 else "DOWN"),
    }
    group_agreement = sum(direction_ok.values())
    # Two methods legitimately diagnose volatility/range, not direction:
    # ATR/spread and Bollinger compression are never treated as a buy vote.
    band = m_adv.get("bollinger_20_2") or {}
    oscillator = m_adv.get("stochastic_14_3") or {}
    # Extreme RSI and stoch in a directional trade can signal chasing risk;
    # they are not an automatic reversal.
    exhausted = ((rsi >= 77 or oscillator.get("k", 50) >= 93) if sign == 1
                 else (rsi <= 23 or oscillator.get("k", 50) <= 7))
    bb_percentile = band.get("bandwidth_percentile_recent")
    near_compression = bb_percentile is not None and bb_percentile <= 20
    score = round(15 + regime_score + (12 if same_side else 0) +
                  min(16, 3.0 * group_agreement) +
                  (3 if near_compression and m_breakout != "NONE" else 0) -
                  (8 if exhausted else 0) -
                  spread_penalty - stretched_penalty, 2)
    return {
        "research_priority": score,
        "observed_oanda_spread_to_m15_atr": round(cost_fraction, 4),
        "m15_rsi14": round(rsi, 2), "m15_adx14": round(adx15, 2),
        "h1_adx14": round(adx1, 2),
        "m15_ema20": ema15, "h1_ema20": ema1,
        "technical_directional_alignment": bool(same_side),
        "research_regime": "TREND" if trending >= ranging else "RANGE",
        "potential_direction_not_approved": direction,
        "technical_direction_checks": direction_ok,
        "technical_checks_agree": group_agreement,
        "stretched_or_exhausted": bool(exhausted),
        "bollinger_compression_recent": bool(near_compression),
        "methods_reviewed": [
            "EMA20/50/200", "ADX/+DI/-DI", "ATR14 vs spread",
            "RSI14", "MACD12/26/9", "Bollinger20/2",
            "Stochastic14/3", "Ichimoku9/26/52",
            "Supertrend10/3", "Donchian20",
        ],
        "high_relative_spread": bool(cost_fraction > 1.5),
        "screening_only": True,
        "note": "Review priority only. Cost, multi-frame structure, news and "
                "forward risk gates must independently qualify every entry.",
    }


def deep_selection(screening, monitored=(), max_extra=18):
    """Every discovered pair gets M15/H1; full M5/H4/D on highest
    review-priority names, original majors and up to three monitored pairs.
    """
    ranked = sorted((pair for pair, data in screening.items()
                     if data.get("research_priority") is not None),
                    key=lambda pair: (-screening[pair]["research_priority"], pair))
    selected = set(ranked[:max_extra])
    selected |= set(CORE) & set(screening)
    selected |= set(monitored) & set(screening)
    return sorted(selected)

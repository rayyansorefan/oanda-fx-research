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
    "AUD_USD", "EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD", "USD_CHF",
    "EUR_GBP", "AUD_NZD", "AUD_CHF", "GBP_AUD", "NZD_USD", "EUR_AUD",
    "GBP_JPY", "EUR_JPY", "NZD_JPY", "GBP_CAD", "CHF_JPY", "AUD_JPY",
    "USD_TRY", "USD_ZAR", "EUR_TRY", "USD_MXN", "USD_SGD", "USD_THB",
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
    """Research-review priority, NOT a profitable-trade probability.

    Reward trend strength on two timeframes and adequate volatility relative
    to the current OANDA spread; penalise very extended RSI and poor pricing.
    Produces both LONG and SHORT candidates equally. Does not issue entries.
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
    if cost_fraction > 0.65:
        return None
    adx15 = m.get("adx14") or 0
    adx1 = h.get("adx14") or 0
    rsi = m.get("rsi14")
    if rsi is None:
        return None
    mid = (quote["bid"] + quote["ask"]) / 2
    ema15 = m.get("ema20")
    ema1 = h.get("ema20")
    if ema15 is None or ema1 is None:
        return None
    agreement = (mid - ema15) * (mid - ema1) > 0
    score = round(
        0.35 * min(60, adx1) +
        0.25 * min(60, adx15) +
        (12 if agreement else 0) +
        max(0, 25 - cost_fraction * 80) -
        max(0, abs(rsi - 50) - 22) * 0.45, 2)
    return {
        "research_priority": score,
        "observed_oanda_spread_to_m15_atr": round(cost_fraction, 4),
        "m15_rsi14": round(rsi, 2),
        "m15_adx14": round(adx15, 2),
        "h1_adx14": round(adx1, 2),
        "m15_ema20": ema15, "h1_ema20": ema1,
        "technical_directional_alignment": bool(agreement),
        "screening_only": True,
        "note": "Rank for full discretionary research; not a signal or proof of edge.",
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

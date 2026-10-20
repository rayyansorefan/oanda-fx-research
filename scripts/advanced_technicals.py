"""Additional independent research descriptors from completed midpoint OHLC bars.

All calculations are descriptive. No module places orders or grades trade signals.
Spot FX volume is venue-specific tick activity, NOT consolidated market volume.
"""
from math import sqrt


def _sma(values):
    return sum(values) / len(values)


def bollinger(rows, period=20, deviations=2):
    if len(rows) < period:
        return None
    closes = [row["mid"]["c"] for row in rows]
    sample = closes[-period:]
    center = _sma(sample)
    std = sqrt(sum((x - center) ** 2 for x in sample) / period)
    top, bottom = center + deviations * std, center - deviations * std
    width = (top - bottom) / center if center else None
    widths = []
    for index in range(period, len(closes) + 1):
        subset = closes[index - period:index]
        middle = _sma(subset)
        variation = sqrt(sum((x - middle) ** 2 for x in subset) / period)
        if middle:
            widths.append(2 * deviations * variation / middle)
    history = widths[-100:]
    percentile = (100 * sum(x <= width for x in history) / len(history)) if width is not None and history else None
    return {"period": period, "std_deviations": deviations, "middle": center,
            "upper": top, "lower": bottom, "relative_bandwidth": width,
            "bandwidth_percentile_recent": percentile,
            "close_position": (closes[-1] - bottom) / (top - bottom) if top > bottom else None}


def stochastic(rows, period=14, smooth_k=3):
    if len(rows) < period + smooth_k - 1:
        return None
    raw = []
    for index in range(period - 1, len(rows)):
        sample = rows[index - period + 1:index + 1]
        high = max(x["mid"]["h"] for x in sample)
        low = min(x["mid"]["l"] for x in sample)
        current_close = rows[index]["mid"]["c"]
        raw.append(100 * (current_close - low) / (high - low) if high > low else 50.)
    k = _sma(raw[-smooth_k:])
    d = _sma([_sma(raw[i - smooth_k + 1:i + 1])
              for i in range(smooth_k - 1, len(raw))][-smooth_k:]) if len(raw) >= 2 * smooth_k - 1 else None
    return {"k": k, "d": d, "period": period, "smoothing": smooth_k}


def swings(rows, left=2, right=2, lookback=100):
    """Confirmed 2/2 fractals. The last 2 candles cannot be called swings."""
    sample = rows[-lookback:]
    highs, lows = [], []
    for index in range(left, len(sample) - right):
        current = sample[index]["mid"]
        neighbours = sample[index - left:index] + sample[index + 1:index + 1 + right]
        if all(current["h"] > row["mid"]["h"] for row in neighbours):
            highs.append({"time": sample[index]["time"], "price": current["h"],
                          "index": len(rows) - len(sample) + index})
        if all(current["l"] < row["mid"]["l"] for row in neighbours):
            lows.append({"time": sample[index]["time"], "price": current["l"],
                         "index": len(rows) - len(sample) + index})
    def trend(points):
        if len(points) < 2:
            return "INSUFFICIENT_CONFIRMED_SWINGS"
        if points[-1]["price"] > points[-2]["price"]:
            return "HIGHER"
        if points[-1]["price"] < points[-2]["price"]:
            return "LOWER"
        return "EQUAL"
    return {"highs": highs[-5:], "lows": lows[-5:],
            "last_high_class": trend(highs), "last_low_class": trend(lows)}


def fib_from_swings(confirmed):
    anchors = sorted(confirmed["highs"] + confirmed["lows"], key=lambda x: x["index"])
    if not anchors:
        return None
    last = anchors[-1]
    prior = [x for x in anchors[:-1] if ("highs" if x in confirmed["highs"] else "lows") !=
             ("highs" if last in confirmed["highs"] else "lows")]
    if not prior:
        return None
    first = prior[-1]
    if first["index"] >= last["index"] or first["price"] == last["price"]:
        return None
    movement = last["price"] - first["price"]
    return {
        "anchor_start": {"time": first["time"], "price": first["price"]},
        "anchor_end": {"time": last["time"], "price": last["price"]},
        "move": "UP" if movement > 0 else "DOWN",
        "retracement": {
            "0.382": last["price"] - movement * 0.382,
            "0.500": last["price"] - movement * 0.500,
            "0.618": last["price"] - movement * 0.618
        },
        "note": "Reference levels only; confirmed 2-left/2-right fractal anchors"
    }


def candle_structure(rows):
    if len(rows) < 2:
        return None
    bar, prior = rows[-1]["mid"], rows[-2]["mid"]
    width = bar["h"] - bar["l"]
    if width <= 0:
        return {"range": 0.0, "pattern": "FLAT"}
    top = max(bar["o"], bar["c"])
    bottom = min(bar["o"], bar["c"])
    engulfing = (bar["c"] > bar["o"] and prior["c"] < prior["o"] and
                 bar["c"] >= prior["o"] and bar["o"] <= prior["c"])
    bearish_engulfing = (bar["c"] < bar["o"] and prior["c"] > prior["o"] and
                         bar["o"] >= prior["c"] and bar["c"] <= prior["o"])
    return {
        "direction": "BULLISH" if bar["c"] > bar["o"] else "BEARISH" if bar["c"] < bar["o"] else "DOJI",
        "range": width,
        "body_fraction": abs(bar["c"] - bar["o"]) / width,
        "upper_wick_fraction": (bar["h"] - top) / width,
        "lower_wick_fraction": (bottom - bar["l"]) / width,
        "close_position_in_range": (bar["c"] - bar["l"]) / width,
        "inside_previous_candle": bar["h"] < prior["h"] and bar["l"] > prior["l"],
        "outside_previous_candle": bar["h"] > prior["h"] and bar["l"] < prior["l"],
        "bullish_engulfing": engulfing,
        "bearish_engulfing": bearish_engulfing,
    }


def rsi_at(closes, index, period=14):
    """Wilder RSI as known at that candle; excludes future data."""
    if index < period:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(1, index + 1)]
    up = sum(max(diff, 0) for diff in diffs[:period]) / period
    down = sum(max(-diff, 0) for diff in diffs[:period]) / period
    for diff in diffs[period:]:
        up = (up * (period - 1) + max(diff, 0)) / period
        down = (down * (period - 1) + max(-diff, 0)) / period
    if down == 0:
        return 100. if up else 50.
    return 100 - 100 / (1 + up / down)


def divergence(rows, confirmed):
    closes = [x["mid"]["c"] for x in rows]
    result = []
    for side, divergence_type in (("highs", "BEARISH_CANDIDATE"), ("lows", "BULLISH_CANDIDATE")):
        points = confirmed[side]
        if len(points) < 2:
            continue
        previous, latest = points[-2:]
        old_rsi = rsi_at(closes, previous["index"])
        new_rsi = rsi_at(closes, latest["index"])
        if old_rsi is None or new_rsi is None:
            continue
        exists = ((latest["price"] > previous["price"] and new_rsi < old_rsi) if side == "highs"
                  else (latest["price"] < previous["price"] and new_rsi > old_rsi))
        if exists:
            result.append({"type": divergence_type, "price_pivots": [previous["price"], latest["price"]],
                           "rsi_at_pivots": [old_rsi, new_rsi], "time": latest["time"],
                           "note": "Unconfirmed RSI divergence candidate, not entry signal"})
    return result


def structure(rows):
    if len(rows) < 40:
        return None
    previous = rows[-21:-1]
    high20 = max(bar["mid"]["h"] for bar in previous)
    low20 = min(bar["mid"]["l"] for bar in previous)
    current = rows[-1]["mid"]
    pivots = swings(rows)
    return {
        "prior_20_bar_high": high20,
        "prior_20_bar_low": low20,
        "latest_close_breakout": "UP" if current["c"] > high20 else
                                 "DOWN" if current["c"] < low20 else "NONE",
        "confirmed_swings": {"recent_highs": [{k: x[k] for k in ("time", "price")}
                                            for x in pivots["highs"]],
                             "recent_lows": [{k: x[k] for k in ("time", "price")}
                                           for x in pivots["lows"]],
                             "highs": pivots["last_high_class"],
                             "lows": pivots["last_low_class"]},
        "fibonacci": fib_from_swings(pivots),
        "rsi_divergence_candidates": divergence(rows, pivots),
        "last_candle": candle_structure(rows),
    }


def advanced(rows):
    return {
        "bollinger_20_2": bollinger(rows),
        "stochastic_14_3": stochastic(rows),
        "structure": structure(rows),
        "volume_context": "OANDA candle volume is broker/tick activity, not consolidated FX volume",
    }

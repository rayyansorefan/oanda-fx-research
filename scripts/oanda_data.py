#!/usr/bin/env python3
"""Read-only OANDA v20 FX snapshot. No orders, accounts changes or trading logic."""
import json
import math
import os
import sys
from advanced_technicals import advanced
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://api-fxpractice.oanda.com"
PAIRS = ("EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD",
         "EUR_GBP", "AUD_USD", "USD_CHF", "EUR_JPY")
FRAMES = ("M5", "M15", "H1", "H4", "D")
OUT = Path("data/latest.json")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def request(path, params=None):
    token = os.environ.get("OANDA_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing OANDA_API_TOKEN secret")
    url = BASE + path + (("?" + urlencode(params)) if params else "")
    req = Request(url, headers={"Authorization": "Bearer " + token,
                                "Accept": "application/json",
                                "Accept-Datetime-Format": "RFC3339"}, method="GET")
    try:
        with urlopen(req, timeout=25) as resp:
            return json.load(resp)
    except HTTPError as exc:
        raise RuntimeError("OANDA_HTTP_" + str(exc.code)) from None
    except (URLError, TimeoutError):
        raise RuntimeError("OANDA_NETWORK_ERROR") from None


def ema(values, period):
    if len(values) < period:
        return None
    avg = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for value in values[period:]:
        avg += alpha * (value - avg)
    return avg


def ema_series(values, period):
    if len(values) < period:
        return []
    avg = sum(values[:period]) / period
    output = [avg]
    for value in values[period:]:
        avg += 2 / (period + 1) * (value - avg)
        output.append(avg)
    return output


def rsi(closes, period=14):
    if len(closes) <= period:
        return None
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(v, 0) for v in changes[:period]) / period
    loss = sum(max(-v, 0) for v in changes[:period]) / period
    for v in changes[period:]:
        gain = (gain * (period - 1) + max(v, 0)) / period
        loss = (loss * (period - 1) + max(-v, 0)) / period
    if loss == 0:
        return 100.0 if gain else 50.0
    return 100 - 100 / (1 + gain / loss)


def macd(closes):
    if len(closes) < 35:
        return None
    fast = ema_series(closes, 12)
    slow = ema_series(closes, 26)
    line = [fast[i + 14] - slow[i] for i in range(len(slow))]
    signal = ema(line, 9)
    return {"line": line[-1], "signal": signal, "histogram": line[-1] - signal}


def atr_adx(rows, period=14):
    if len(rows) < period * 3:
        return None, None, None, None
    tr, plus, minus = [], [], []
    for a, b in zip(rows, rows[1:]):
        h, l, previous_close = b["mid"]["h"], b["mid"]["l"], a["mid"]["c"]
        up = h - a["mid"]["h"]
        down = a["mid"]["l"] - l
        tr.append(max(h - l, abs(h - previous_close), abs(l - previous_close)))
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    smooth_tr, smooth_plus, smooth_minus = map(sum, (tr[:period], plus[:period], minus[:period]))
    dxs = []
    def dx_of(t, p, m):
        pd = 100 * p / t if t else 0.0
        md = 100 * m / t if t else 0.0
        return (100 * abs(pd - md) / (pd + md) if pd + md else 0.0, pd, md)
    dx, pd, md = dx_of(smooth_tr, smooth_plus, smooth_minus)
    dxs.append(dx)
    for index in range(period, len(tr)):
        smooth_tr = smooth_tr - smooth_tr / period + tr[index]
        smooth_plus = smooth_plus - smooth_plus / period + plus[index]
        smooth_minus = smooth_minus - smooth_minus / period + minus[index]
        dx, pd, md = dx_of(smooth_tr, smooth_plus, smooth_minus)
        dxs.append(dx)
    adx = sum(dxs[:period]) / period
    for value in dxs[period:]:
        adx = (adx * (period - 1) + value) / period
    return smooth_tr / period, adx, pd, md


def compact_candle(candle):
    out = {"time": candle["time"], "volume": candle.get("volume", 0)}
    for side in ("mid", "bid", "ask"):
        if side not in candle:
            raise ValueError("Missing candle side: " + side)
        out[side] = {k: float(candle[side][k]) for k in ("o", "h", "l", "c")}
        if not out[side]["l"] <= out[side]["o"] <= out[side]["h"]:
            raise ValueError("Invalid OHLC values")
    return out


def describe(candles):
    rows = [compact_candle(x) for x in candles if x.get("complete") is True]
    if len(rows) < 210:
        raise ValueError("INSUFFICIENT_COMPLETED_CANDLES")
    closes = [x["mid"]["c"] for x in rows]
    atr, adx, plus, minus = atr_adx(rows)
    recent = rows[-20:]
    prior = rows[-2]["mid"]
    pivot = (prior["h"] + prior["l"] + prior["c"]) / 3
    last = rows[-1]
    return {
        "completed_at": last["time"],
        "completed_at_field_note": "OANDA candle opening timestamp; complete=true confirms candle closed",
        "complete_candles_used": len(rows),
        "indicators_from": "OANDA completed midpoint candles, independently calculated",
        "indicators": {
            "ema20": ema(closes, 20), "ema50": ema(closes, 50),
            "ema200": ema(closes, 200), "rsi14": rsi(closes),
            "macd_12_26_9": macd(closes), "atr14": atr,
            "adx14": adx, "plus_di14": plus, "minus_di14": minus,
            "resistance20": max(x["mid"]["h"] for x in recent),
            "support20": min(x["mid"]["l"] for x in recent),
            "pivot_previous_bar": pivot,
            "r1_previous_bar": 2 * pivot - prior["l"],
            "s1_previous_bar": 2 * pivot - prior["h"]
        },
        "advanced": advanced(rows),
        "recent_completed_candles": rows[-96:]
    }


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False) + "\n")


def collect():
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "").strip()
    if not account_id:
        accounts = request("/v3/accounts").get("accounts", [])
        if len(accounts) != 1:
            raise RuntimeError("Set OANDA_ACCOUNT_ID secret: expected exactly one account")
        account_id = accounts[0]["id"]
    prices = request("/v3/accounts/" + account_id + "/pricing",
                     {"instruments": ",".join(PAIRS), "includeHomeConversions": "false",
                      "includeUnitsAvailable": "false"})
    quotes = {}
    for p in prices.get("prices", []):
        if p.get("instrument") not in PAIRS:
            continue
        bids, asks = p.get("bids", []), p.get("asks", [])
        if not bids or not asks:
            continue
        bid, ask = float(bids[0]["price"]), float(asks[0]["price"])
        quotes[p["instrument"]] = {
            "time": p["time"], "bid": bid, "ask": ask,
            "spread_pips": (ask - bid) / (0.01 if p["instrument"].endswith("_JPY") else 0.0001),
            "tradeable": bool(p.get("tradeable", False))
        }
    if len(quotes) != len(PAIRS):
        raise RuntimeError("MISSING_PAIRS_IN_PRICING")
    output = {"status": "OK", "collected_at": now(),
              "source": "OANDA v20 practice (not Pepperstone executable prices)",
              "quote_type": "OANDA account-specific bid/ask, best available price",
              "pairs": {}}
    for pair in PAIRS:
        frames = {}
        for frame in FRAMES:
            response = request("/v3/instruments/" + pair + "/candles",
                               {"granularity": frame, "count": 250, "price": "MBA",
                                "smooth": "false", "dailyAlignment": 17,
                                "alignmentTimezone": "America/New_York"})
            frames[frame] = describe(response.get("candles", []))
        output["pairs"][pair] = {"quote": quotes[pair], "frames": frames}
    write_json(OUT, output)
    summary = {
        "status": output["status"], "collected_at": output["collected_at"],
        "source": output["source"],
        "candle_time_note": "Each completed_at is OANDA candle OPEN; bars are complete=true",
        "pairs": {}
    }
    for instrument, row in output["pairs"].items():
        summary["pairs"][instrument] = {
            "quote": row["quote"],
            "frames": {frame: {
                "completed_at": values["completed_at"],
                "indicators": values["indicators"],
                "advanced": values["advanced"],
                "last_3_completed_candles": values["recent_completed_candles"][-3:]
            } for frame, values in row["frames"].items()}
        }
    write_json(Path("data/summary.json"), summary)
    print("Collected", len(output["pairs"]), "pairs; status=OK; at", output["collected_at"])


if __name__ == "__main__":
    try:
        collect()
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        code = str(exc)
        if not (code.startswith("OANDA_") or code in
                ("INSUFFICIENT_COMPLETED_CANDLES", "MISSING_PAIRS_IN_PRICING")
                or code.startswith("Missing ") or code.startswith("Set ")
                or code.startswith("Invalid ") or code.startswith("Missing candle")):
            code = type(exc).__name__
        failure = {"status": "DATA_UNAVAILABLE", "collected_at": now(),
                   "error": code, "pairs": {}}
        write_json(OUT, failure)
        write_json(Path("data/summary.json"), failure)
        print("Data unavailable:", code, file=sys.stderr)
        sys.exit(1)

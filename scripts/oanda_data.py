#!/usr/bin/env python3
"""Read-only OANDA v20 FX snapshot. No orders, accounts changes or trading logic."""
import json
import math
import os
import sys
import time
from advanced_technicals import advanced
from fx_universe import catalog, chunks, is_quote_usable, screen_priority, deep_selection, CORE
from datetime import datetime, timezone
from pathlib import Path
import shutil
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://api-fxpractice.oanda.com"
# All OANDA account CURRENCY instruments are discovered dynamically every run.
# Never hardcode the historical 8-pair test universe as an execution limit.
FRAMES = ("M5", "M15", "H1", "H4", "D")
OUT = Path("data/latest.json")
CANDLE_CACHE = {}
# Bounded global retry allowance: transient OANDA gateway failures must not
# turn a six-minute, research-only hourly job into an unbounded API loop.
TRANSIENT_RETRIES_LEFT = 8


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
    global TRANSIENT_RETRIES_LEFT
    for attempt in range(2):
        try:
            with urlopen(req, timeout=15) as resp:
                return json.load(resp)
        except HTTPError as exc:
            if (exc.code in (429, 502, 503, 504) and attempt == 0
                    and TRANSIENT_RETRIES_LEFT > 0):
                TRANSIENT_RETRIES_LEFT -= 1
                time.sleep(0.5)
                continue
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


def describe(candles, frame="M15", deep=True):
    rows = [compact_candle(x) for x in candles if x.get("complete") is True]
    if len(rows) < 210:
        raise ValueError("INSUFFICIENT_COMPLETED_CANDLES")
    closes = [x["mid"]["c"] for x in rows]
    atr, adx, plus, minus = atr_adx(rows)
    recent = rows[-20:]
    prior = rows[-2]["mid"]
    pivot = (prior["h"] + prior["l"] + prior["c"]) / 3
    last = rows[-1]
    # Retain enough chronological data for the day's audit without sending
    # unnecessary multi-day history into the user's four ChatGPT reviews.
    stored_bars = ({"M5": 96, "M15": 96, "H1": 60, "H4": 36, "D": 30}.get(frame, 48)
                   if deep else 12)
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
        "recent_completed_candles": rows[-stored_bars:]
    }


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False) + "\n")


def fetch_quote_batch(account_id, instruments, discovered):
    quotes = {}
    problems = {}
    for group in chunks(instruments, 35):
        try:
            raw = request("/v3/accounts/" + account_id + "/pricing",
                          {"instruments": ",".join(group),
                           "includeHomeConversions": "false",
                           "includeUnitsAvailable": "false"})
        except RuntimeError as exc:
            for pair in group:
                problems[pair] = "OANDA_PRICING_ERROR_" + str(exc)[:70]
            continue
        for record in raw.get("prices", []):
            pair = record.get("instrument")
            if pair not in discovered:
                continue
            try:
                bids, asks = record.get("bids", []), record.get("asks", [])
                if not bids or not asks:
                    raise ValueError("NO_BID_ASK")
                bid, ask = float(bids[0]["price"]), float(asks[0]["price"])
                pip = discovered[pair]["pip_size"]
                quotes[pair] = {
                    "time": record["time"], "bid": bid, "ask": ask,
                    "spread_pips": (ask - bid) / pip,
                    "pip_size": pip, "tradeable": bool(record.get("tradeable", False))
                }
            except (KeyError, ValueError, TypeError):
                problems[pair] = "INVALID_QUOTE"
    for pair in instruments:
        if pair not in quotes and pair not in problems:
            problems[pair] = "NO_QUOTE_IN_BATCH_RESPONSE"
    return quotes, problems


def fetch_frame(pair, frame, deep=True):
    response = request("/v3/instruments/" + pair + "/candles",
                       {"granularity": frame, "count": 250, "price": "MBA",
                        "smooth": "false", "dailyAlignment": 17,
                        "alignmentTimezone": "America/New_York"})
    raw = response.get("candles", [])
    if not deep:
        CANDLE_CACHE[(pair, frame)] = raw
    return describe(raw, frame, deep=deep)


def brief_frame(values):
    return {
        "completed_at": values["completed_at"],
        "indicators": values["indicators"],
        "advanced": values["advanced"],
        "last_3_completed_candles": values["recent_completed_candles"][-3:]
    }


def currently_monitored_names():
    try:
        signals = json.loads(Path("signals.json").read_text()).get("signals", [])
        return [s["instrument"] for s in signals if isinstance(s, dict) and "instrument" in s]
    except (OSError, ValueError, TypeError, KeyError):
        return []


def collect():
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "").strip()
    if not account_id:
        accounts = request("/v3/accounts").get("accounts", [])
        if len(accounts) != 1:
            raise RuntimeError("Set OANDA_ACCOUNT_ID secret: expected exactly one account")
        account_id = accounts[0]["id"]
    instruments = request("/v3/accounts/" + account_id + "/instruments").get("instruments", [])
    discovered = catalog(instruments)
    if not discovered:
        raise RuntimeError("OANDA_NO_FX_CURRENCIES_DISCOVERED")
    names = sorted(discovered)
    quotes, problems = fetch_quote_batch(account_id, names, discovered)
    valid = [pair for pair in names if pair in quotes and is_quote_usable(quotes[pair])]
    if not valid:
        raise RuntimeError("OANDA_NO_USABLE_FX_QUOTES")
    # Phase 1: scan EVERY usable OANDA account currency pair M15 and H1.
    # The 250 complete bid/ask/mid candles are used to compute all indicators.
    # Only 12 recent bars each are persisted for non-finalist pairs.
    all_data = {}
    ranking = {}
    full_failures = dict(problems)
    # Scan liquid majors first so gateway outages do not disproportionately
    # hide the instruments needed for the four daily discretionary reviews.
    for pair in sorted(names, key=lambda item: (item not in CORE, item)):
        if pair not in valid:
            if pair in quotes and not quotes[pair].get("tradeable"):
                full_failures[pair] = "OANDA_NON_TRADEABLE_QUOTE"
            elif pair in quotes:
                full_failures[pair] = "OANDA_BAD_SPREAD_OR_PRICE"
            continue
        try:
            m15 = fetch_frame(pair, "M15", deep=False)
            h1 = fetch_frame(pair, "H1", deep=False)
            score = screen_priority(quotes[pair], m15, h1)
            all_data[pair] = {"quote": quotes[pair], "frames": {"M15": m15, "H1": h1}}
            if score is not None:
                ranking[pair] = score
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            full_failures[pair] = ("SCANNER_" + type(exc).__name__ +
                                   "_" + str(exc)[:55]).replace("OANDA_HTTP_", "HTTP_")
    if not all_data:
        raise RuntimeError("OANDA_NO_FX_TECHNICAL_HISTORY")
    # Phase 2: full D/H4/H1/M15/M5 analysis on twelve strongest candidates,
    # original majors and approved watchlist. Every other FX pair still has
    # an independent M15/H1 indicator screen this same hourly collection.
    monitored = currently_monitored_names()
    selection = deep_selection(ranking, monitored=monitored, max_extra=12)
    deep_ok = []
    for pair in selection:
        if pair not in all_data:
            continue
        frames = all_data[pair]["frames"]
        try:
            # Use the first pass's M15 and H1 calculations; no duplicate requests.
            for frame in ("M5", "H4", "D"):
                frames[frame] = fetch_frame(pair, frame, deep=True)
            # Full chronology needed by the prospective setup watcher.
            frames["M15"] = describe(CANDLE_CACHE[(pair, "M15")], "M15", deep=True)
            frames["H1"] = describe(CANDLE_CACHE[(pair, "H1")], "H1", deep=True)
            deep_ok.append(pair)
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            full_failures[pair] = "DEEP_" + type(exc).__name__ + "_" + str(exc)[:55]
    # If a known monitored pair is quoted but could not load technical history,
    # never imply it is being reliably watched.
    payload = {
        "status": "OK" if len(all_data) == len(names) and len(deep_ok) == len(selection) else "PARTIAL",
        "collected_at": now(),
        "source": "OANDA v20 practice (not Pepperstone executable prices)",
        "quote_type": "OANDA account-specific bid/ask, best available price",
        "coverage": {
            "oanda_account_fx_instruments_discovered": len(discovered),
            "oanda_pairs_with_usable_live_quotes": len(valid),
            "oanda_pairs_with_m15_h1_indicators": len(all_data),
            "pairs_deep_d1_h4_h1_m15_m5": len(deep_ok),
            "deep_pairs": deep_ok,
            "pairs_missing_or_partial": full_failures,
            "oanda_not_pepperstone": "FX pairs found in this OANDA account do NOT prove availability on a UK Pepperstone spread-betting account.",
            "pepperstone_uk_90plus": "The complete UK Pepperstone spread-bet symbol catalogue has not been authenticated. Do not claim 100% broker coverage.",
            "unmatched_pepperstone_pairs": "UNKNOWN: cannot infer missing Pepperstone-only symbols from OANDA list."
        },
        "pairs": all_data
    }
    # Persist lightweight coverage and catalog for audits; no tokens/account IDs.
    write_json(Path("data/universe.json"), {
        "collected_at": payload["collected_at"],
        "coverage": payload["coverage"],
        "instruments": discovered,
        "source": "OANDA account /instruments, NOT a Pepperstone contract list"
    })
    summary = {
        "status": payload["status"],
        "collected_at": payload["collected_at"],
        "source": payload["source"],
        "candle_time_note": "Each completed_at is OANDA candle OPEN; bars are complete=true",
        "coverage": payload["coverage"],
        "ranked_review_candidates": [
            {"instrument": pair, **ranking[pair],
             "pepperstone_spreadbet_account_availability": "UNVERIFIED",
             "pepperstone_uk_publicly_referenced": discovered[pair]["pepperstone_uk_publicly_referenced"]}
            for pair in sorted(ranking, key=lambda p: (-ranking[p]["research_priority"], p))[:35]
        ],
        "pairs": {
            pair: {"quote": content["quote"],
                   "pepperstone_spreadbet_account_availability": "UNVERIFIED",
                   "deep_analysis_available": pair in deep_ok,
                   "frames": {frame: brief_frame(values)
                              for frame, values in content["frames"].items()}}
            for pair, content in all_data.items()
        }
    }
    # Keep public GitHub feed under the Contents API 1MB threshold. Store
    # full OHLC for at least the top 12 deeply screened candidates plus any
    # approved immutable setup being monitored, NOT merely two spread-filtered
    # names. All 65+ remaining pairs retain M15/H1 indicators each cycle.
    audit_names = set(deep_ok) & (
        set(sorted(ranking, key=lambda p: (-ranking[p]["research_priority"], p))[:12])
        | set(monitored))
    audit_dir = Path("data/pairs")
    if audit_dir.exists():
        shutil.rmtree(audit_dir)
    for pair in sorted(audit_names):
        write_json(audit_dir / (pair + ".json"), {
            "status": "OK", "collected_at": payload["collected_at"],
            "source": payload["source"], "instrument": pair,
            "quote": payload["pairs"][pair]["quote"],
            "frames": payload["pairs"][pair]["frames"],
            "notice": "Research snapshots, not Pepperstone executable quotes"
        })
    payload["coverage"]["detailed_audit_pairs"] = sorted(audit_names)
    payload["coverage"]["detailed_audit_dir"] = "data/pairs/{instrument}.json"
    # This second final manifest is authoritative: the earlier one is written
    # before detailed audit filenames are selected. Do not leave stale metadata.
    write_json(Path("data/universe.json"), {
        "collected_at": payload["collected_at"],
        "coverage": payload["coverage"],
        "instruments": discovered,
        "source": "OANDA account /instruments, NOT a Pepperstone contract list"
    })
    summary["coverage"] = payload["coverage"]
    for pair, item in payload["pairs"].items():
        for value in item["frames"].values():
            # Summary retains independently computed advanced technicals.
            # Latest is a compact timestamped hourly event/price monitor.
            value["recent_completed_candles"] = value["recent_completed_candles"][-12:]
            value.pop("advanced", None)
    write_json(OUT, payload)
    write_json(Path("data/summary.json"), summary)
    print("OANDA account discovered", len(discovered), "FX pairs; screened",
          len(all_data), "M15/H1; deep", len(deep_ok),
          "; status", payload["status"], "; at", payload["collected_at"])


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
        write_json(Path("data/universe.json"), failure)
        audit_dir = Path("data/pairs")
        if audit_dir.exists():
            shutil.rmtree(audit_dir)
        print("Data unavailable:", code, file=sys.stderr)
        sys.exit(1)

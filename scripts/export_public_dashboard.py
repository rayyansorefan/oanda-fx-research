#!/usr/bin/env python3
"""Publish strictly whitelisted FX research to the public HTML dashboard.

Requires a fine-grained GitHub token scoped to the destination repo
with Contents read/write. No OANDA account credentials or trading execution.
"""
from __future__ import annotations
import base64
import datetime as dt
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

DESTINATION = "rayyansorefan/standalone-wallet-paper-agent"
TARGET = "docs/fx-feed.json"
STATUS_PATH = Path("data/public_dashboard_status.json")

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def compact_frame(f: dict, full: bool) -> dict:
    i = f.get("indicators") or {}
    advanced = f.get("advanced") or {}
    obj = {
        "complete_open_at": f.get("completed_at"),
        "indicators": {
            "ema20": i.get("ema20"), "ema50": i.get("ema50"),
            "ema200": i.get("ema200"), "rsi14": i.get("rsi14"),
            "adx14": i.get("adx14"), "plus_di14": i.get("plus_di14"),
            "minus_di14": i.get("minus_di14"), "atr14": i.get("atr14"),
            "support20": i.get("support20"), "resistance20": i.get("resistance20"),
            "macd_12_26_9": i.get("macd_12_26_9"),
            "bollinger": advanced.get("bollinger_20_2"),
            "stochastic": advanced.get("stochastic_14_3"),
            "supertrend": advanced.get("supertrend_10_3"),
            "ichimoku": advanced.get("ichimoku_9_26_52"),
            "donchian": advanced.get("donchian_20"),
            "structure": advanced.get("structure"),
        },
    }
    if full:
        bars = f.get("recent_completed_candles") or []
        obj["candles"] = [
            {
                "t": bar.get("time"),
                "o": (bar.get("mid") or {}).get("o"),
                "h": (bar.get("mid") or {}).get("h"),
                "l": (bar.get("mid") or {}).get("l"),
                "c": (bar.get("mid") or {}).get("c"),
            } for bar in bars[-40:]
        ]
    return obj

def safe_quote(q: dict) -> dict:
    return {k: q.get(k) for k in ("time", "bid", "ask", "spread_pips", "pip_size", "tradeable")}


# The hourly ChatGPT health check has a 950 KB JSON ceiling. Keep a
# deliberate margin for growth in indicator metadata and new chart frames.
PUBLIC_MAX_BYTES = 930_000

def serialized_size(payload: dict) -> int:
    return len((json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"))

def fit_public_feed_budget(payload: dict) -> None:
    """Trim only historical OHLC density, never current quotes or research setups."""
    if serialized_size(payload) <= PUBLIC_MAX_BYTES:
        return
    for cap in (32, 24, 16, 12, 8, 6):
        for pair in payload.get("pairs", {}).values():
            if not pair.get("deep"):
                continue
            for frame in (pair.get("frames") or {}).values():
                candles = frame.get("candles")
                if isinstance(candles, list) and len(candles) > cap:
                    frame["candles"] = candles[-cap:]
        if serialized_size(payload) <= PUBLIC_MAX_BYTES:
            return
    raise ValueError("Public FX payload remains over 930000 bytes with six candles per detailed frame")

def build_research_setups(summary: dict, limit: int = 3) -> list[dict]:
    """Prospective technical planning levels, never approved entry alerts.

    These are derived only from the *current* completed-bar summary.  We do not
    retrospectively label historical prices as entries and do not issue orders.
    """
    pairs = summary.get("pairs") or {}
    setups = []
    for candidate in summary.get("ranked_review_candidates") or []:
        key = candidate.get("instrument")
        direction = candidate.get("potential_direction_not_approved")
        if direction not in ("LONG", "SHORT") or candidate.get("technical_checks_agree", 0) < 4:
            continue
        source = pairs.get(key) or {}
        quote, frames = source.get("quote") or {}, source.get("frames") or {}
        if quote.get("tradeable") is not True:
            continue
        try:
            pip = float(quote["pip_size"])
            spread = float(quote["spread_pips"]) * pip
            bid, ask = float(quote["bid"]), float(quote["ask"])
            m = (frames["M15"]["indicators"])
            h = (frames["H1"]["indicators"])
            h4 = (frames["H4"]["indicators"])
            day = (frames["D"]["indicators"])
            atr, ema = float(m["atr14"]), float(m["ema20"])
            high, low = float(m["resistance20"]), float(m["support20"])
            adx, h1_adx = float(m["adx14"]), float(h["adx14"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if not (0 < pip < atr and 0 < spread <= .5 * atr
                and high > low and bid > 0 and ask > bid
                and adx >= 20 and h1_adx >= 20):
            continue
        trend = []
        for frame in (m, h, h4, day):
            try:
                a, b = float(frame["ema20"]), float(frame["ema50"])
            except (KeyError, TypeError, ValueError):
                trend = []
                break
            trend.append("LONG" if a > b else "SHORT" if a < b else "NEUTRAL")
        if len(trend) != 4 or any(t != direction for t in trend):
            continue
        if abs(ema - (bid if direction == "SHORT" else ask)) > 2.5 * atr:
            continue  # Do not publish a stale/chasing pullback zone.
        entry_low, entry_high = ema - .15 * atr, ema + .15 * atr
        if direction == "SHORT":
            stop = max(high + .15 * atr, entry_high + 1.1 * atr)
            worst_entry = entry_low
            distance = stop - worst_entry
            tp1, tp2 = worst_entry - 1.85 * distance, worst_entry - 2.35 * distance
        else:
            stop = min(low - .15 * atr, entry_low - 1.1 * atr)
            worst_entry = entry_high
            distance = worst_entry - stop
            tp1, tp2 = worst_entry + 1.85 * distance, worst_entry + 2.35 * distance
        if not (.8 * atr <= distance <= 4 * atr):
            continue
        net_rr = (1.85 * distance - spread) / (distance + spread)
        if net_rr < 1.35:
            continue
        places = 3 if pip >= .01 else 5
        setups.append({
            "instrument": key, "direction": direction,
            "status": "CONDITIONAL_RESEARCH_NOT_TRIGGERED",
            "model": "M15 EMA20 trend-pullback / rejection",
            "research_priority": round(float(candidate.get("research_priority") or 0), 2),
            "technical_checks_agree": candidate["technical_checks_agree"],
            "timeframes_aligned": ["M15", "H1", "H4", "D"],
            "entry_low": round(entry_low, places),
            "entry_high": round(entry_high, places),
            "stop": round(stop, places),
            "tp1": round(tp1, places), "tp2": round(tp2, places),
            "net_rr_estimate": round(net_rr, 2),
            "source_quote_at": quote.get("time"),
            "trigger": ("Wait for a completed M15 bearish rejection within the zone."
                        if direction == "SHORT" else
                        "Wait for a completed M15 bullish rejection within the zone."),
            "broker_check": "Verify live Pepperstone spread, exact execution price and account availability.",
            "news_check": "Check scheduled macro events and official news before entry.",
            "approved_entry": False,
        })
        if len(setups) >= limit:
            break
    return setups

def build_public_feed(summary: dict, news: dict, signals: dict,
                      watcher: dict, detailed: dict, brief_history: dict | None = None) -> dict:
    coverage = summary.get("coverage") or {}
    prices = {}
    for key, source in (summary.get("pairs") or {}).items():
        if not isinstance(source, dict) or not isinstance(source.get("quote"), dict):
            continue
        if not key.replace("_", "").isalpha() or "_" not in key:
            continue
        shard = detailed.get(key) or {}
        lightweight = (brief_history or {}).get(key) or {}
        frames = {}
        for period in ("M1", "M5", "M15", "M30", "H1", "H4", "D", "W", "M"):
            frame = (shard.get("frames") or {}).get(period) or (source.get("frames") or {}).get(period)
            if frame and (shard or period in ("M15", "H1")):
                frames[period] = compact_frame(frame, bool(shard))
                # All 65+ screened instruments must be chart-selectable: expose
                # the 12 last completed midpoint candles for M15/H1 even when
                # a pair was not chosen for the full multi-timeframe audit.
                if not shard and period in ("M15", "H1"):
                    observations = ((lightweight.get("frames") or {})
                                    .get(period) or {}).get("recent_completed_candles") or []
                    frames[period]["candles"] = [
                        {"t": bar.get("time"),
                         **{dst: (bar.get("mid") or {}).get(src)
                            for dst, src in (("o", "o"), ("h", "h"),
                                             ("l", "l"), ("c", "c"))}}
                        for bar in observations[-12:]
                        if isinstance(bar, dict)
                    ]
        prices[key] = {
            "name": key.replace("_", "/"),
            "quote": safe_quote(source["quote"]),
            "frames": frames,
            "deep": bool(shard),
        }
    raw_signals = signals.get("signals") or []
    allowed_signal_fields = (
        "id", "instrument", "direction", "created_at", "expires_at",
        "planned_entry", "stop", "tp1", "tp2", "alert_a", "alert_b",
        "max_oanda_spread_pips", "event_blackouts_utc",
    )
    clean_signals = [{k: s.get(k) for k in allowed_signal_fields}
                     for s in raw_signals if isinstance(s, dict)]
    headlines = [
        {"title": h.get("title"), "source": h.get("source"),
         "url": h.get("url"), "time": h.get("published_at"), "verified": False}
        for h in (news.get("recent_material_headlines") or [])[:16]
        if isinstance(h, dict)
    ]
    calendar = [
        {"title": e.get("title"), "time": e.get("time"),
         "currency": e.get("currency"), "impact": e.get("impact"),
         "actual": e.get("actual"), "forecast": e.get("forecast"),
         "official": e.get("verified_by_official_source") is True}
        for e in (news.get("calendar") or {}).get("events", [])[:50]
        if isinstance(e, dict)
    ]
    source_entries = [
        {"name": key, "status": v.get("status"), "checked_at": v.get("checked_at")}
        for key, v in (news.get("sources") or {}).items() if isinstance(v, dict)
    ]
    result = {
        "schema": 1,
        "snapshot_at": summary.get("collected_at"),
        "news_at": news.get("collected_at"),
        "published_at": utc_now(),
        "status": summary.get("status"),
        "news_status": news.get("status"),
        "coverage": {
            "total": coverage.get("oanda_account_fx_instruments_discovered", 0),
            "quoted": coverage.get("oanda_pairs_with_usable_live_quotes", 0),
            "screened": coverage.get("oanda_pairs_with_m15_h1_indicators", 0),
            "deep": len([x for x in detailed.values() if isinstance(x, dict)]),
        },
        "priorities": [
            {"pair": r.get("instrument"), "priority": r.get("research_priority")}
            for r in (summary.get("ranked_review_candidates") or [])
            if isinstance(r, dict)
        ],
        "pairs": prices,
        "signals": clean_signals,
        "research_setups": build_research_setups(summary),
        "hourly": {
            "at": watcher.get("collected_at"),
            "counts": watcher.get("counts"),
            "signals": watcher.get("signals") or [],
        },
        "sources": source_entries,
        "headlines": headlines,
        "calendar": calendar,
        "learning": {
            "verified_broker_trades": 0,
            "verified_wins": 0,
            "verified_losses": 0,
        },
    }
    if not result["snapshot_at"] or not prices:
        raise ValueError("No valid source quote collection; refusing to publish.")
    fit_public_feed_budget(result)
    text = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
    for term in ("OANDA_API_TOKEN", "OANDA_ACCOUNT_ID", "FX_DASHBOARD_DEPLOY_TOKEN"):
        if term in text:
            raise ValueError("Potential credential field detected; refusing to publish.")
    return result

def status(state: str, details: str = "", snapshot_at: str | None = None) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps({
        "checked_at": utc_now(), "status": state, "reason": details,
        "snapshot_at": snapshot_at, "public_repository": DESTINATION,
        "public_feed_path": TARGET,
    }, indent=2) + "\n", encoding="utf-8")
    print("FX public feed:", state, details[:150])

def _github_error(action: str, exc: urllib.error.HTTPError) -> RuntimeError:
    """Report GitHub's safe permission explanation, never credentials or headers."""
    message = ""
    try:
        response = json.loads(exc.read(4096).decode("utf-8", errors="replace"))
        if isinstance(response, dict) and isinstance(response.get("message"), str):
            message = response["message"][:240]
    except (ValueError, UnicodeError, OSError):
        pass
    return RuntimeError(f"GitHub {action} HTTP {exc.code}: {message or 'No explanation'}")


def _existing_public_feed(url: str, headers: dict) -> tuple[str, dict]:
    """Read the actual public-repository file, not an unverified local copy."""
    request = urllib.request.Request(url, headers={**headers, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            current = json.load(response)
    except urllib.error.HTTPError as exc:
        raise _github_error("public feed GET", exc) from None
    if current.get("encoding") != "base64" or not current.get("content") or not current.get("sha"):
        raise ValueError("Cannot verify existing public feed before write")
    published = json.loads(base64.b64decode(current["content"]).decode("utf-8"))
    return current["sha"], published


def _timestamp(text: str) -> dt.datetime:
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("FX snapshot timestamps must be timezone-aware")
    return value.astimezone(dt.timezone.utc)


def publish(payload: dict, token: str) -> str:
    """Safely publish and read back the exact candidate; never overwrite newer data.

    409/422 concurrent-write conflicts are retried after reading the new
    remote content. A GitHub commit acknowledgement alone is insufficient.
    """
    url = "https://api.github.com/repos/" + DESTINATION + "/contents/" + TARGET
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fx-research-dashboard-publisher",
    }
    candidate_time = _timestamp(payload["snapshot_at"])
    encoded = base64.b64encode(
        (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).decode("ascii")
    for attempt in range(4):
        blob_sha, current = _existing_public_feed(url, headers)
        previous_time = _timestamp(current["snapshot_at"])
        if previous_time > candidate_time:
            return "SUPERSEDED"
        if current == payload:
            return "ALREADY_CURRENT"
        # Equal market snapshots can have later independent news/watchlist
        # changes; an older snapshot must never win against a newer one.
        if previous_time == candidate_time:
            old_publication = _timestamp(current.get("published_at", current["snapshot_at"]))
            new_publication = _timestamp(payload.get("published_at", payload["snapshot_at"]))
            if old_publication >= new_publication:
                return "ALREADY_CURRENT"
        body = {
            "message": "data: publish sanitized FX research snapshot",
            "sha": blob_sha, "branch": "main", "content": encoded,
        }
        update = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"}, method="PUT",
        )
        try:
            with urllib.request.urlopen(update, timeout=30) as response:
                saved = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 422) and attempt < 3:
                continue
            raise _github_error("public feed PUT", exc) from None
        if not (saved.get("commit") or {}).get("sha"):
            raise ValueError("GitHub did not confirm a dashboard commit")
        # Verify the committed bytes through an independent Contents GET.
        # GitHub Pages deployment is verified separately by the public workflow.
        for read_attempt in range(3):
            _, verified = _existing_public_feed(url, headers)
            if verified == payload:
                return "PUBLISHED"
            import time
            time.sleep(2)
        raise ValueError("Public repository readback did not match the candidate payload")
    raise RuntimeError("Public feed changed repeatedly; bounded retries exhausted")


PUBLIC_PAYLOAD_PATH = Path("data/public_dashboard_payload.json")

def main() -> int:
    # Generate a strictly whitelisted payload. Without an authorised
    # cross-repository deploy token, fail visibly instead of claiming success.
    token = os.environ.get("FX_DASHBOARD_DEPLOY_TOKEN", "").strip()
    try:
        summary = load("data/summary.json")
        news = load("data/news_latest.json")
        recent = load("data/latest.json")
        if recent.get("collected_at") != summary.get("collected_at"):
            raise ValueError("Mismatched OHLC and summary collection timestamps")
        signals = load("signals.json")
        watcher = load("data/hourly_assessment.json")
        detailed = {}
        for name in (summary.get("coverage") or {}).get("detailed_audit_pairs", []):
            path = Path("data/pairs") / (name + ".json")
            if path.is_file():
                row = json.loads(path.read_text(encoding="utf-8"))
                if row.get("instrument") == name and row.get("collected_at") == summary.get("collected_at"):
                    detailed[name] = row
        data = build_public_feed(summary, news, signals, watcher, detailed,
                                 recent.get("pairs") or {})
        if not data.get("snapshot_at") or len(data.get("pairs", {})) < 1:
            raise ValueError("Invalid sanitized payload")
        PUBLIC_PAYLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC_PAYLOAD_PATH.write_text(
            json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        if not token:
            status("NOT_CONFIGURED",
                   "FX_DASHBOARD_DEPLOY_TOKEN missing. Public dashboard NOT updated.",
                   data["snapshot_at"])
            return 1
        outcome = publish(data, token)
        status(outcome, "Public repository contents readback verified." if outcome == "PUBLISHED"
               else "Remote feed was already as recent or newer; no older overwrite.",
               data["snapshot_at"])
        return 0
    except urllib.error.HTTPError as e:
        status("FAILED", "GitHub status " + str(e.code))
    except Exception as e:
        status("FAILED", type(e).__name__ + ": " + str(e)[:350])
    return 1

if __name__ == "__main__":
    raise SystemExit(main())

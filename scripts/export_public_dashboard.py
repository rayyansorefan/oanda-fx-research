#!/usr/bin/env python3
"""Publish strictly whitelisted FX research to the public HTML dashboard.

Requires an optional fine-grained GitHub token scoped to the destination repo
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
            } for bar in bars[-70:]
        ]
    return obj

def safe_quote(q: dict) -> dict:
    return {k: q.get(k) for k in ("time", "bid", "ask", "spread_pips", "pip_size", "tradeable")}

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
        for period in ("M5", "M15", "H1", "H4", "D"):
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

def publish(payload: dict, token: str) -> None:
    url = "https://api.github.com/repos/" + DESTINATION + "/contents/" + TARGET
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fx-research-dashboard-publisher",
    }
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        current = json.load(response)
    blob_sha = current.get("sha")
    if not blob_sha:
        raise ValueError("Destination feed blob SHA not found")
    body = {
        "message": "data: publish sanitized FX research snapshot",
        "sha": blob_sha,
        "branch": "main",
        "content": base64.b64encode(
            (json.dumps(payload, ensure_ascii=True, separators=(",", ":"))+"\n").encode("utf-8")
        ).decode("ascii"),
    }
    update = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"}, method="PUT",
    )
    with urllib.request.urlopen(update, timeout=25) as response:
        saved = json.load(response)
    if not (saved.get("commit") or {}).get("sha"):
        raise ValueError("GitHub did not confirm a commit")

PUBLIC_PAYLOAD_PATH = Path("data/public_dashboard_payload.json")

def main() -> int:
    # Generate the same strictly whitelisted public feed even when no
    # cross-repository personal access token exists. The connected ChatGPT
    # GitHub bridge can then publish it using its existing OAuth permission.
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
            status("BRIDGE_READY",
                   "Whitelisted private payload prepared for connected GitHub bridge; public publication not yet confirmed.",
                   data["snapshot_at"])
            return 0
        publish(data, token)
        status("PUBLISHED", "Sanitized public dashboard research updated.", data["snapshot_at"])
        return 0
    except urllib.error.HTTPError as e:
        status("FAILED", "GitHub status " + str(e.code))
    except Exception as e:
        status("FAILED", type(e).__name__ + ": " + str(e)[:100])
    return 1

if __name__ == "__main__":
    raise SystemExit(main())

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
from paper_trade_audit import public_view
from adaptive_research import afternoon_audit
from entry_readiness import entry_diagnostics

DESTINATION = "rayyansorefan/standalone-wallet-paper-agent"
TARGET = "docs/fx-feed.json"
STATUS_PATH = Path("data/public_dashboard_status.json")
# Broker-unavailable instruments must never enter the public research dashboard.
# Keep them in the internal OANDA research universe; filter at export.
DASHBOARD_EXCLUDED_INSTRUMENTS = frozenset({"GBP_ZAR"})

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
PUBLIC_MAX_BYTES = 920_000
PUBLIC_HARD_MAX_BYTES = 930_000

def serialized_size(payload: dict) -> int:
    return len((json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"))

def fit_public_feed_budget(payload: dict) -> None:
    """Keep current quotes and all published research economics immutable.

    Prefer reducing optional historical candle density before touching paper
    audit histories. Full observations and histories remain in the private ledger.
    If the public rolling window must be bounded, disclose exact coverage.
    """
    audit = ((payload.get("learning") or {}).get("paper_audit") or {})
    candidates = audit.get("candidates") or []
    if not isinstance(candidates, list):
        raise ValueError("Invalid public paper audit candidates")
    # The front-page paper trade blotter must always show audited scored outcomes,
    # even if the full rolling public candidate window is capped for file size.
    # Source rows are paper research only; never include private replay evidence,
    # broker account identifiers, or monetary results in the public payload.
    scored_keys = ("id", "instrument", "direction", "created_at", "strategy",
                   "entry", "stop", "tp1", "status", "route", "triggered_at",
                   "entry_at", "at", "exit", "move_pct", "scored")
    audit["featured_scored"] = [
        {key: row[key] for key in scored_keys if row.get(key) is not None}
        for row in candidates
        if row.get("scored") is True and row.get("status") in
        ("WIN_TP1", "LOSS_STOP", "TIME_EXIT")
    ][-20:]
    # Compact full-universe index; the detailed rolling audit remains bounded.
    # These observations are NOT fills, realised return, or live entry alerts.
    # Fixed tuple fields keep ALL registered observations affordable as the
    # hourly cohort grows. UI supports both former dict and new tuple shapes.
    audit["candidate_index_fields"] = [
        "id", "pair", "side", "created_at", "status", "entry",
        "stop", "tp1", "move_pct", "quote_at", "reference_price"
    ]
    audit["candidate_index"] = [
        [row.get("id"), row.get("instrument"), row.get("direction"),
         row.get("created_at"), row.get("status"), row.get("entry"),
         row.get("stop"), row.get("tp1"), row.get("move_pct"),
         (row.get("tracking") or {}).get("quote_at"),
         (row.get("tracking") or {}).get("reference_price")]
        for row in candidates
    ]
    audit["public_window"] = {
        "published_candidates": len(candidates),
        "private_total_candidates": int((audit.get("totals") or {}).get("candidates", len(candidates))),
        "truncated": int((audit.get("totals") or {}).get("candidates", len(candidates))) > len(candidates),
        "policy": "Public rolling window may omit older candidates; full history remains private."
    }
    if serialized_size(payload) <= PUBLIC_MAX_BYTES:
        return
    # Preserve at least 24 recent completed candles per deep frame where
    # possible, and preserve all latest indicator values and quote timestamps.
    for cap in (48, 40, 32, 24, 16, 12, 8, 6):
        for pair in payload.get("pairs", {}).values():
            if not pair.get("deep"):
                continue
            for frame in (pair.get("frames") or {}).values():
                candles = frame.get("candles")
                if isinstance(candles, list) and len(candles) > cap:
                    frame["candles"] = candles[-cap:]
        if serialized_size(payload) <= PUBLIC_MAX_BYTES:
            return
    # Preserve at least one observed price point per candidate: an empty
    # public timeline breaks the all-candidate tracker and Pages smoke tests.
    # Never remove frozen candidate economics or replace scored results.
    for history_cap in (6, 3, 1):
        for row in candidates:
            tracking = row.get("tracking") or {}
            history = tracking.get("history")
            if isinstance(history, list) and len(history) > history_cap:
                tracking["history"] = history[-history_cap:]
        if serialized_size(payload) <= PUBLIC_MAX_BYTES:
            return
    # Non-deep M15/H1 plots can use a slightly shorter rolling window;
    # preserve at least six bars, live quote, and the latest indicators.
    # This is preferable to erasing every candidate's observed timeline.
    for candle_cap in (11, 10, 8, 6):
        for pair in payload.get("pairs", {}).values():
            if pair.get("deep"):
                continue
            for frame in (pair.get("frames") or {}).values():
                candles = frame.get("candles")
                if isinstance(candles, list) and len(candles) > candle_cap:
                    frame["candles"] = candles[-candle_cap:]
        if serialized_size(payload) <= PUBLIC_MAX_BYTES:
            return
    # Bounded public window must be explicit, never silently claim complete
    # coverage. Keep latest rows; full historical ledger stays private.
    for cap in (90, 75, 60, 45, 30):
        if len(candidates) > cap:
            # Keep ALL genuinely scored paper outcomes visible even when older
            # than the public rolling window. Remaining space goes to newest
            # unscored observations; never relabel or recalculate outcomes.
            scored = [row for row in candidates if row.get("scored") is True
                      and row.get("status") in ("WIN_TP1", "LOSS_STOP", "TIME_EXIT")]
            if len(scored) > cap:
                kept = scored[-cap:]
            else:
                identifiers = {row.get("id") for row in scored}
                recent = [row for row in candidates if row.get("id") not in identifiers]
                kept = scored + recent[-(cap - len(scored)):] if cap > len(scored) else scored
            audit["candidates"] = kept
            audit["public_window"]["truncated"] = True
            audit["public_window"]["published_candidates"] = len(audit["candidates"])
        if serialized_size(payload) <= PUBLIC_MAX_BYTES:
            return
    if serialized_size(payload) <= PUBLIC_MAX_BYTES:
        return
    # Preserve every registered ID in candidate_index while reducing duplicated
    # optional rich excerpts. The complete per-watch histories stay private.
    # A larger deep-research universe must not prevent the public dashboard
    # from publishing fresh prices and genuine approved signals.
    opportunity = ((payload.get("learning") or {}).get("opportunity_audit") or {})
    recent = opportunity.get("recent")
    if isinstance(recent, list):
        for cap in (40, 24, 12):
            if len(recent) > cap:
                opportunity["recent"] = recent[-cap:]
                window = opportunity.setdefault("public_window", {})
                window["detailed_rows"] = len(opportunity["recent"])
                window["truncated"] = len(opportunity["recent"]) < int(
                    window.get("total_rows") or len(recent))
            if serialized_size(payload) <= PUBLIC_MAX_BYTES:
                return
    # Hourly and learning sections both carry a full afternoon excerpt; a
    # concise recent window is enough on the public page. Exact full records
    # are retained in private hourly_assessment.json and paper ledgers.
    for cap in (12, 6, 0):
        for section in ((payload.get("hourly") or {}).get("afternoon_audit"),
                        ((payload.get("learning") or {}).get("afternoon_challenger"))):
            if not isinstance(section, dict):
                continue
            entries = section.get("candidates")
            if isinstance(entries, list) and len(entries) > cap:
                section["candidates"] = entries[-cap:] if cap else []
                section["public_candidates_truncated"] = True
        if serialized_size(payload) <= PUBLIC_MAX_BYTES:
            return
    if serialized_size(payload) <= PUBLIC_HARD_MAX_BYTES:
        return
    raise ValueError("Public FX payload exceeds hard limit after safe compaction; publication refused")

def build_research_setups(summary: dict, limit: int = 3) -> list[dict]:
    """Prospective technical planning levels, never approved entry alerts.

    These are derived only from the *current* completed-bar summary.  We do not
    retrospectively label historical prices as entries and do not issue orders.
    """
    pairs = summary.get("pairs") or {}
    setups = []
    for candidate in summary.get("ranked_review_candidates") or []:
        key = candidate.get("instrument")
        if key in DASHBOARD_EXCLUDED_INSTRUMENTS:
            continue
        direction = candidate.get("potential_direction_not_approved")
        if direction not in ("LONG", "SHORT") or candidate.get("technical_checks_agree", 0) < 4:
            continue
        if (candidate.get("adaptive_context") or {}).get("block_new_alert") is True:
            continue  # Reversal/extension warning: no new prospective entry zones.
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
        if len(trend) != 4 or trend[0] != direction or trend[1] != direction:
            continue  # The M15 and H1 directional foundation must agree.
        aligned_count = sum(t == direction for t in trend)
        if aligned_count < 3:
            continue  # One opposing higher frame is watchable, two are not.
        full_alignment = aligned_count == 4
        conflicts = [frame for frame, state in zip(("M15", "H1", "H4", "D"), trend)
                     if state != direction]
        executable = bid if direction == "SHORT" else ask
        distance_in_trend = (ema - executable if direction == "SHORT"
                             else executable - ema)
        recent = frames["M15"].get("last_3_completed_candles") or []
        # A prospective entry must have an independently completed swing.
        # Do not use the much more distant 20-bar channel extreme as a
        # mandatory pullback stop when closer completed M15 structure exists.
        if len(recent) < 3:
            continue
        try:
            recent_high = max(float(c["mid"]["h"]) for c in recent[-3:])
            recent_low = min(float(c["mid"]["l"]) for c in recent[-3:])
        except (KeyError, TypeError, ValueError):
            continue
        continuation = False
        # An additional *prospective* route: a strong already-completed M15
        # impulse may justify monitoring the next M5 continuation. A perfect
        # EMA20 retest is not mandatory; do not chase extended impulses.
        if .35 * atr <= distance_in_trend <= 1.5 * atr and recent:
            try:
                bar = recent[-1]["mid"]
                opening, close = float(bar["o"]), float(bar["c"])
                bottom, top = float(bar["l"]), float(bar["h"])
                span = top - bottom
                m_plus, m_minus = float(m["plus_di14"]), float(m["minus_di14"])
                h_plus, h_minus = float(h["plus_di14"]), float(h["minus_di14"])
                directional = ((opening > close and m_minus > m_plus and h_minus > h_plus)
                               if direction == "SHORT" else
                               (close > opening and m_plus > m_minus and h_plus > h_minus))
                strong_close = ((top - close) / span >= .70 if direction == "SHORT"
                                else (close - bottom) / span >= .70) if span > 0 else False
                continuation = (span > 0 and abs(close - opening) / span >= .50
                                and directional and strong_close
                                and abs(executable - close) <= .35 * atr)
            except (KeyError, ValueError, TypeError, ZeroDivisionError):
                continuation = False
        # A conflicting higher timeframe is useful for EARLY RESEARCH ONLY.
        # It must never become the guarded four-frame continuation candidate.
        if not full_alignment:
            continuation = False
        # Evaluate both independently: continuation has a shorter entry window.
        for route_continuation in ((True, False) if continuation else (False,)):
            if route_continuation:
                entry_low, entry_high = executable - .12 * atr, executable + .12 * atr
                # Use the same frozen completed swing for both routes.
            else:
                if abs(ema - executable) > 2.5 * atr:
                    continue
                entry_low, entry_high = ema - .15 * atr, ema + .15 * atr
            if direction == "SHORT":
                stop = max(recent_high + .15 * atr, entry_high + 1.1 * atr)
                worst_entry = entry_low
                distance = stop - worst_entry
                # Nearest genuine completed support must justify the reward.
                targets = sorted({
                    float(f["support20"]) for f in (m, h, h4, day)
                    if f.get("support20") is not None and float(f["support20"]) < worst_entry
                }, reverse=True)
            else:
                stop = min(recent_low - .15 * atr, entry_low - 1.1 * atr)
                worst_entry = entry_high
                distance = worst_entry - stop
                targets = sorted({
                    float(f["resistance20"]) for f in (m, h, h4, day)
                    if f.get("resistance20") is not None and float(f["resistance20"]) > worst_entry
                })
            if not targets or not (.8 * atr <= distance <= 4 * atr):
                continue
            tp1, tp2 = targets[0], targets[1] if len(targets) > 1 else None
            gross_rr = abs(tp1 - worst_entry) / distance
            net_rr = (abs(tp1 - worst_entry) - spread) / (distance + spread)
            if gross_rr < 1.50 or net_rr < 1.35:
                continue
            places = 3 if pip >= .01 else 5
            setups.append({
                "instrument": key, "direction": direction,
                "status": "CONDITIONAL_RESEARCH_NOT_TRIGGERED",
                "model": ("M15 impulse / next M5 continuation" if route_continuation else
                          "M15 EMA20 trend-pullback / rejection"),
                "entry_route": "MOMENTUM_CONTINUATION" if route_continuation else "EMA20_PULLBACK",
                "research_priority": round(float(candidate.get("research_priority") or 0), 2),
                "technical_checks_agree": candidate["technical_checks_agree"],
                "timeframes_aligned": [
                    frame for frame, trend_direction in zip(("M15", "H1", "H4", "D"), trend)
                    if trend_direction == direction],
                "higher_timeframe_conflicts": conflicts,
                "research_only_blocker": (
                    "Higher-timeframe conflict on " + ", ".join(conflicts) +
                    "; directional watch ONLY; NOT eligible for live/manual signal admission."
                    if conflicts else
                    "Await an independently completed entry candle and fresh news/spread review."
                ),
                "entry_low": round(entry_low, places),
                "entry_high": round(entry_high, places),
                "stop": round(stop, places),
                "tp1": round(tp1, places),
                "tp2": round(tp2, places) if tp2 is not None else None,
                "target_basis": "nearest completed M15/H1/H4/D 20-bar support/resistance",
                "stop_basis": "last three completed M15 swing plus volatility floor",
                "net_rr_estimate": round(net_rr, 2),
                "source_quote_at": quote.get("time"),
                "impulse_m15_started_at": (recent[-1].get("time") if recent and route_continuation else None),
                "m5_confirmation": "PENDING_NEW_COMPLETED_M5" if route_continuation else "REQUIRES_COMPLETED_M15_REJECTION",
                "trigger": (("Wait for a NEW completed M5 candle continuing lower, without "
                             "a mandatory EMA retest; reconfirm M15/H1 trend and spread."
                             if direction == "SHORT" else
                             "Wait for a NEW completed M5 candle continuing higher, without "
                             "a mandatory EMA retest; reconfirm M15/H1 trend and spread.")
                            if route_continuation else
                            ("Wait for a completed M15 bearish rejection within the zone."
                             if direction == "SHORT" else
                             "Wait for a completed M15 bullish rejection within the zone.")),
                "broker_check": "Verify live Pepperstone spread, exact execution price and account availability.",
                "news_check": "Check scheduled macro events and official news before entry.",
                "approved_entry": False,
                "execution_status": "WATCH_ONLY_NOT_APPROVED",
            })
            if len(setups) >= limit:
                return setups
    return setups

def _public_opportunity_view(book):
    # Local import avoids a module-level dependency cycle: the tracker uses
    # build_research_setups, while the exporter only needs its safe public view.
    try:
        from scripts.opportunity_tracker import public_view
    except ModuleNotFoundError:
        from opportunity_tracker import public_view
    return public_view(book)

def build_entry_gate_summary(summary: dict, news: dict) -> dict:
    """Count every researched admission blocker, not just the eight desk rows.

    Counts are descriptive research diagnostics and never admit or alter a
    canonical signal. This exposes systematic bottlenecks for prospective
    challenger studies without weakening costs, news or execution rules.
    """
    count = len(summary.get("ranked_review_candidates") or []) + 3
    rows = entry_diagnostics(summary, news, limit=max(8, count))
    hard = {"STALE_QUOTE", "QUOTE_UNUSABLE", "COST_HIGH_OR_UNKNOWN",
            "FOUNDATION_CONFLICT", "HIGHER_FRAME_CONFLICT", "HIGHER_FRAME_MISSING", "TREND_STRENGTH",
            "DMI_CONFLICT", "OVEREXTENDED_OR_REVERSING", "M15_BAR_STALE",
            "M15_BAR_TIME", "M15_HISTORY", "PRICE_LEVEL_UNAVAILABLE",
            "STRUCTURAL_REWARD"}
    blockers = {}
    no_technical_block = 0
    priced_research_previews = 0
    for row in rows:
        codes = {item.get("code") for item in row.get("blockers") or []
                 if isinstance(item, dict) and item.get("code")}
        for code in codes:
            blockers[code] = blockers.get(code, 0) + 1
        no_technical_block += int(not bool(codes & hard))
        priced_research_previews += int(bool(row.get("priced_paper_preview")))
    return {
        "as_of": summary.get("collected_at"),
        "evaluated": len(rows), "technical_or_cost_gate_clear": no_technical_block,
        "priced_paper_research_previews": priced_research_previews,
        "blocker_counts": dict(sorted(blockers.items())),
        "news_status": news.get("status") or "UNKNOWN",
        "canonical_signals_admitted": False,
        "note": "Diagnostic counts overlap and do not represent filled trades, win rates or approved signals.",
    }


def build_public_feed(summary: dict, news: dict, signals: dict,
                      watcher: dict, detailed: dict, brief_history: dict | None = None,
                      paper_ledger: dict | None = None, opportunity_ledger: dict | None = None) -> dict:
    coverage = summary.get("coverage") or {}
    prices = {}
    for key, source in (summary.get("pairs") or {}).items():
        if key in DASHBOARD_EXCLUDED_INSTRUMENTS:
            continue
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
        "max_oanda_spread_pips", "event_blackouts_utc", "alert_c",
        "entry_route", "gross_rr_at_creation", "net_rr_at_spread_cap",
        "broker_verified", "manual_broker_check_required", "account_equity_verified",
    )
    clean_signals = [{k: s.get(k) for k in allowed_signal_fields}
                     for s in raw_signals if isinstance(s, dict)
                     and s.get("instrument") not in DASHBOARD_EXCLUDED_INSTRUMENTS]
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
            "total": max(0, (coverage.get("oanda_account_fx_instruments_discovered") or len(prices))
                         - len(DASHBOARD_EXCLUDED_INSTRUMENTS & set(summary.get("pairs") or {}))),
            "quoted": len(prices),
            "screened": max(0, (coverage.get("oanda_pairs_with_m15_h1_indicators") or 0)
                            - sum(bool(((summary.get("pairs") or {}).get(k) or {}).get("frames", {}).get("M15")
                                       and ((summary.get("pairs") or {}).get(k) or {}).get("frames", {}).get("H1"))
                                  for k in DASHBOARD_EXCLUDED_INSTRUMENTS)),
            "deep": sum(bool(p.get("deep")) for p in prices.values()),
        },
        "priorities": [
            {"pair": r.get("instrument"), "priority": r.get("research_priority"),
             "adaptive_status": (r.get("adaptive_context") or {}).get("status") if r.get("adaptive_context") else None}
            for r in (summary.get("ranked_review_candidates") or [])
            if isinstance(r, dict) and r.get("instrument") not in DASHBOARD_EXCLUDED_INSTRUMENTS
        ],
        "pairs": prices,
        "signals": clean_signals,
        "research_setups": build_research_setups(summary),
        "hourly": {
            "at": watcher.get("collected_at"),
            "counts": watcher.get("counts"),
            "afternoon_audit": watcher.get("afternoon_audit"),
            "signals": [s for s in (watcher.get("signals") or [])
                        if isinstance(s, dict) and
                        (s.get("pair") or s.get("instrument")) not in DASHBOARD_EXCLUDED_INSTRUMENTS],
        },
        "sources": source_entries,
        "headlines": headlines,
        "calendar": calendar,
        "learning": {
            "paper_audit": public_view(paper_ledger or {"candidates": {}}),
            "afternoon_challenger": watcher.get("afternoon_audit") or afternoon_audit(
                paper_ledger or {"candidates": {}}, summary["collected_at"]),
            "opportunity_audit": _public_opportunity_view(opportunity_ledger or {}),
            # Explicit research-only failed entry gates, never canonical signals.
            "entry_diagnostics": entry_diagnostics(summary, news, limit=8),
            "entry_gate_summary": build_entry_gate_summary(summary, news),
            "verified_broker_trades": 0,
            "verified_wins": 0,
            "verified_losses": 0,
        },
    }
    if not result["snapshot_at"] or not prices:
        raise ValueError("No valid source quote collection; refusing to publish.")
    fit_public_feed_budget(result)
    # A future publisher change must not accidentally reintroduce unavailable symbols.
    for item in result["research_setups"]:
        if item.get("instrument") in DASHBOARD_EXCLUDED_INSTRUMENTS:
            raise ValueError("Unavailable broker instrument in public setups")
    if DASHBOARD_EXCLUDED_INSTRUMENTS & set(result["pairs"]):
        raise ValueError("Unavailable broker instrument in public pairs")
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
        paper_path = Path("data/paper_trade_ledger.json")
        paper = load(str(paper_path)) if paper_path.is_file() else {"candidates": {}}
        opportunity_path = Path("data/entry_opportunity_ledger.json")
        opportunity = load(str(opportunity_path)) if opportunity_path.is_file() else {}
        data = build_public_feed(summary, news, signals, watcher, detailed,
                                 recent.get("pairs") or {}, paper, opportunity)
        comparison_path = Path("data/strategy_comparison.json")
        if comparison_path.exists():
            comparison = load(str(comparison_path))
            data.setdefault("learning", {})["strategy_comparison"] = {
                "updated_at": comparison.get("updated_at"),
                "metrics": comparison.get("metrics"),
                "paper_only": True, "performance_validated": False,
                "promotion_allowed": False}
            fit_public_feed_budget(data)
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

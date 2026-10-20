# OANDA FX Research — Free, read-only bridge

This repository contains a small **market-data collector and price-only alert watcher**, not a trading engine. It never submits orders, reads Pepperstone positions, uses Floot, or consumes ChatGPT API credits.

## What it collects

The collector uses OANDA practice REST GET requests to retrieve current account-specific bid/ask quotes, plus 250 OANDA bid/ask/mid candles for EUR/USD, GBP/USD, USD/JPY, USD/CAD, EUR/GBP, AUD/USD, USD/CHF and EUR/JPY on M5/M15/H1/H4/D. It excludes unfinished candles and calculates EMA20/50/200, RSI14, MACD12/26/9, ADX/DMI14, ATR14, 20-bar support/resistance and previous-bar pivots from midpoint candles.

Output: **data/latest.json** (private repository). Check `status`, `collected_at`, each quote `time`, candle `completed_at` and source labels. Each OANDA candle's `time` is its *opening* timestamp. OANDA quotes are **not Pepperstone executable quotes**.

## Schedule — hourly only

The repository checks once per weekday hour at **06:07–18:07 UTC** via GitHub Actions. This is 13 planned updates per weekday, not every 15 minutes. Scheduling is best-effort and can run late or fail; GitHub minutes are separate from ChatGPT usage. The existing **four weekday ChatGPT FX briefings** remain the only full analysis reports; there are no new ChatGPT tasks.

Up to three explicitly approved ideas in `signals.json` can be **reassessed hourly** for objective completed-M15 continuation or rejection patterns. If one qualifies, the watcher creates a GitHub Issue with `[FX WATCH]` in the title, saying **reassess now**. It does not automatically deliver a ChatGPT message or know whether Pepperstone would fill. If no ideas are approved, the correct result is **zero** trade alerts.

An hourly watcher can miss short-lived opportunities. It cannot replace live bid/ask confirmation, predict news outcomes or provide a guaranteed daily number of positions. News and official releases must be checked independently in the four main ChatGPT reports. An issue alert also requires a fresh macro and Pepperstone check before opening a discretionary trade.

## First-run checklist

1. Store the OANDA practice-account key as GitHub Actions secret `OANDA_API_TOKEN`, never in source or reports. The key previously pasted into chat should preferably be rotated.
2. If OANDA lists multiple accounts, save an additional secret named `OANDA_ACCOUNT_ID` with the intended practice account's ID.
3. Check **Settings → Actions → General**: Actions enabled; workflow `GITHUB_TOKEN` allowed to write repository contents and issues.
4. Run **Actions → OANDA FX read-only research data → Run workflow**, then inspect `data/latest.json`. Require `status: OK`, all eight pairs and fresh source timestamps. A green job alone is not enough.
5. To see GitHub issue notifications, use repository **Watch → Custom → Issues** and configure your GitHub notification preferences. Verify actual notification delivery before depending on it.
6. Check **https://github.com/settings/billing** for remaining included Actions minutes, and set a **$0 Actions spending budget with stop usage**, if available. Other private repositories share the same account allowance.

## How to propose a monitored setup

`signals.json` starts with an empty `signals` array. Only add ideas that have passed the four-main-report technical, macro, correlation and risk checks; a maximum of three simultaneously. Each requires an immutable ID, instrument, direction LONG/SHORT, UTC creation and expiry, alert A and/or B (omit an unjustified scenario), planned entry, stop, TP1/TP2, maximum OANDA spread and explicit high-impact event blackout windows. Never set levels merely to force a signal. A GitHub issue is **not** a market order, Pepperstone quote, confirmed fill or profit.

## Failures and verification

`python -m unittest discover -s tests -v` runs independent tests without any API key. API errors, missing account secrets, missing quotes, malformed history or insufficient completed bars must fail closed. Any `DATA_UNAVAILABLE` snapshot is not a usable trade signal. Audit the GitHub Actions run history, latest quote and candle timestamps, issue notifications and `data/watch_state.json`. The system does not retain a full professional trading journal or calculate real-world net expectancy; ChatGPT reports must preserve actual unfilled and losing outcomes and mark gaps.

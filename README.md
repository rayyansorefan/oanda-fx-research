# OANDA FX Research — Free, read-only bridge

This repository contains a small **market-data collector and hourly news/macro/price reassessment watcher**, not a trading engine. It never submits orders, reads Pepperstone positions, uses Floot, or consumes ChatGPT API credits.

## What it collects

The collector uses OANDA practice REST GET requests to retrieve current account-specific bid/ask quotes, plus 250 OANDA bid/ask/mid candles for EUR/USD, GBP/USD, USD/JPY, USD/CAD, EUR/GBP, AUD/USD, USD/CHF and EUR/JPY on M5/M15/H1/H4/D. It excludes unfinished candles and calculates EMA20/50/200, RSI14, MACD12/26/9, ADX/DMI14, ATR14, Bollinger20/2, Stochastic14/3, confirmed 2/2 swings, market structure, Fibonacci reference levels, RSI divergence candidates, candle anatomy, 20-bar support/resistance and previous-bar pivots from midpoint candles.

Outputs: **data/summary.json** (lightweight technical research brief) and **data/latest.json** (full intraday audit candles, private repository). The working protocol is **docs/RESEARCH_PROTOCOL.md**. Check `status`, `collected_at`, each quote `time`, candle `completed_at` and source labels. Each OANDA candle's `time` is its *opening* timestamp. OANDA quotes are **not Pepperstone executable quotes**.

## Schedule — hourly only

The repository checks once per weekday hour at **06:27–18:27 UTC** via GitHub Actions. This is 13 planned updates per weekday, not every 15 minutes. Scheduling is best-effort and can run late or fail; GitHub minutes are separate from ChatGPT usage. The existing **four weekday ChatGPT FX briefings** remain the only full analysis reports; there are no new ChatGPT tasks.

Up to three explicitly approved ideas in `signals.json` are **reassessed hourly** against newly completed M15 price patterns AND automatically collected macroeconomic headlines, relevant central-bank news, upcoming calendar events, spreads and remaining risk/reward. If one qualifies, the watcher can create a GitHub Issue with `[FX WATCH]` in the title; relevant new material news creates a separate `[FX NEWS REVIEW]` issue, saying **reassess now — do not enter solely on this alert**. It does not automatically deliver a ChatGPT message or know whether Pepperstone would fill. If no ideas are approved, the correct result is **zero** trade alerts.

An hourly watcher can miss short-lived opportunities. It cannot replace broker bid/ask confirmation, guarantee coverage of all breaking news, interpret headlines as a human researcher would, predict economic outcomes or provide a guaranteed daily number of positions. The four main ChatGPT reports still do the complete technical, fundamental and primary-source news analysis. An issue alert also requires a fresh macro and Pepperstone check before opening a discretionary trade.

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

## Handoff to four full reports

The four existing ChatGPT scheduled reports must use authenticated GitHub connector reads of `data/summary.json` at each run, and `data/latest.json` when reconstructing historical candle paths. They must check both snapshot/quote timestamps, verify scheduled economic news separately, and update `signals.json` through the GitHub connector only if a justified actionable watch idea survives all checks. **No automatic AI sentiment service or additional ChatGPT tasks**. See [research protocol](docs/RESEARCH_PROTOCOL.md). A private GitHub API connection may not be available inside every scheduled run: this handoff requires separate end-to-end verification; otherwise reports must say DATA UNAVAILABLE, WATCHLIST NOT UPDATED.

## Hourly news sources and fail-safe rules

`scripts/news_watch.py` retrieves free official Federal Reserve, ECB, Bank of England, Bank of Canada, Bank of Japan, Reserve Bank of Australia, Swiss National Bank, and BLS feeds, one free third-party market-headline aggregation feed and a weekly public economic calendar. It saves `data/news_latest.json` and records source availability. The watcher writes the combined up-to-three-trades research checkpoint to `data/hourly_assessment.json`. Headlines are unverified news alerts, **not validated economic forecasts**; a third-party calendar is provisional until checked against primary sources at each comprehensive ChatGPT review.

For an approved idea, a recent relevant headline or imminent high-impact event suppresses a price-only recommendation and flags `REASSESS_NEWS_FIRST`. A data gap is **NEWS_COVERAGE_NOT_VERIFIED**; it must not silently be reported as a clear market. Price reactions are measured against completed OANDA candles, but correlation is not evidence that the event *caused* the move. Every proposed real-money trade still requires current Pepperstone quotes, event checks, and independent human judgment.

This is best-effort once per hour; not 24-hour news monitoring, not real-time, and may be delayed by GitHub scheduler or individual feeds. GitHub Issues notifications depend on your account Watch/Notifications settings and have not yet been validated for a genuine approved-signal event.

# Research protocol: four full assessments plus hourly data-only monitoring

**Purpose:** The research is discretionary guidance for manually placed Pepperstone FX spread bets. Neither the collector nor watcher makes live trades, interprets headlines, checks Pepperstone pricing, or guarantees profit.

## Authoritative sources (private repository)

- **data/summary.json:** compact latest quotes and the full set of independently calculated indicators by pair/timeframe. Primary input to each scheduled ChatGPT briefing.
- **data/latest.json:** actual timestamped OANDA bid/ask/mid OHLC candles; use when auditing chronological price paths, verifying proposed entries, breakout/retests, stop or target touches. Has multiple previous hours of M5 and M15 bars. Do not judge intra-candle sequence from OHLC.
- **signals.json:** up to three **previously researched** discretionary setup specifications (one instrument/direction per idea), prepared by the scheduled ChatGPT researcher when it has write access. Empty is correct if there are no defensible signals.
- **data/watch_state.json:** hourly watcher state to avoid duplicate issue alerts. Historical alerts are not filled trades.
- **GitHub Issues [FX WATCH]:** price-only technical reassessment notifications. Market news and current Pepperstone dealing spread remain unchecked.

Read using the connected GitHub plugin, repository **rayyansorefan/oanda-fx-research**. **Do not rely on search snippets, inferred links or the public web as a substitute for the authenticated private data.** Check the snapshot's \`status\`, \`collected_at\`, every quote \`time\` and \`tradeable\`, and the candle's opening time with \`complete=true\`. The historical candle time reported under \`completed_at\` is the OPEN of the last completed bar, not its close. Never call a 33-minute-old hourly sample a current broker quote.

If authenticated repo read or data validation fails, state DATA UNAVAILABLE and withhold executable entries. The scheduled ChatGPT task and GitHub Actions workflow are independent; an enabled schedule is not proof of successful source access.

## Four complete reports, UK time

- 07:30: screen eight liquid majors/crosses across D1/H4/H1/M15 (M5 confirmation only). Independently verify economic releases and major breaking news. Rank 0–3 technically and fundamentally supported ideas. For each, A continuation and B pullback alert **only if each is genuinely justified**, exact conditional trigger, stop, TP1/TP2, cost-aware R:R, expiry, macro blackouts, disqualifiers and confidence limitations. Keep unqualified ideas out.
- 12:00: preserve immutable 07:30 recommendations, reconstruct candle paths, distinguish alert touching from actually confirmed and realistically executable fills, audit false/missed trades; recheck macro and revise/revoke only forward from the new timestamp.
- Pre-NYSE (09:00 America/New_York): news actual-vs-consensus, U.S. yields, risk sentiment, USD, bank statements; update opportunity candidates, reject stale/small-R:R entries, and publish any valid additions/replacements in the watchlist.
- 16:30: audit original and revisions, current performance assumptions and near-term risk, decide KEEP/MODIFY/CANCEL/EXPIRED; retain both winning and losing/unfilled evidence, with no hindsight entry reconstruction as actual P&L.

All reports analyse: price market structure, previous day high/low, H4/H1 trend and M15 candles, independent EMA20/50/200 alignment, RSI14, RSI pivot divergence candidates, MACD(12,26,9), ADX14/+DI/−DI, ATR14, Bollinger20/2, Stochastic14/3, confirmed swing highs/lows, Fibonacci 0.382/0.5/0.618 anchored only to verifiable swings, 20-bar support/resistance, pivots, candles and liquidity. No single indicator decides the trade. Avoid interpreting FX broker tick-volume as consolidated volume. Prioritize directional, regime, entry-time, volatility and macro agreement.

## Approved watcher handoff

Before writing \`signals.json\`, read and preserve the existing version. Use GitHub fetch_file then update_file with the exact blob SHA to prevent losing concurrent changes. A new report creates a new immutable ID; never silently modify an old signal's price fields after issue creation or fill. Replace expired/cancelled ideas and preserve past setups in written report records. **Never invent a price** to satisfy a 3-ideas-per-day quota. If repo writes are unavailable, report "watchlist NOT UPDATED" with reasons rather than claiming the hourly GitHub watcher is monitoring proposed trades.

One live idea should look like this, with every numeric and temporal field derived from verified source data rather than examples:

~~~
{
 "id": "20261021-0730-GBPUSD-L-01",
 "instrument": "GBP_USD",
 "direction": "LONG",
 "created_at": "2026-10-21T06:30:00Z",
 "expires_at": "2026-10-21T15:30:00Z",
 "alert_a": 1.23456,
 "alert_b": 1.23210,
 "planned_entry": 1.23470,
 "stop": 1.23150,
 "tp1": 1.23950,
 "tp2": 1.24280,
 "max_oanda_spread_pips": 1.5,
 "event_blackouts_utc": [{"start": "2026-10-21T12:15:00Z", "end": "2026-10-21T12:45:00Z"}]
}
~~~

**The above is purely a structural schema example containing invented prices; it is NEVER a recommendation or instruction to put a trade on.** The \`created_at\` field must represent the genuine issue time (not retroactive). Use UTC correctly with UK daylight saving. If a scenario is unsupported, omit its \`alert_a\` or \`alert_b\` rather than populating an arbitrary level.

The automated watcher only checks OANDA technical candles from the selected setup. It cannot automatically verify news or Pepperstone's executable price. Its alert is **REASSESS NOW**; not BUY NOW or SELL NOW. It screens historical M15 crossings but refuses if the quote is stale, planned TP1 already reached, planned stop passed, or remaining reward relative to risk inadequate. Hourly execution can be delayed and an old event can be over by the time the issue arrives.

## Execution and forward learning

Never open/close trades automatically. The user makes the final Pepperstone trade decision. Indicative risk <=0.25% of verified current account equity per idea; no invented balance or £/point size. The three positions per day is an aspiration, not a quota. Avoid three highly correlated USD exposures, stop inflation and trades into high-impact news. Headlines are not predictable; scenario analysis is different from claiming foreknowledge of releases.

Journal prospectively: timestamp, instrument, scenario A or B, planned entry/SL/TP, OANDA alert and candle close, actual Pepperstone entry/exit only if supplied, spreads, outcomes in R net of known costs, exclusions, expiry and misses. Analyze missed opportunities and false entries symmetrically with wins. Overfitting 1–2 days of market data is not evidence of profitable strategy.

## What is verified / what is NOT

- OANDA authenticated live practice-account data retrieval passed its initial test (GitHub Actions run 39486937460, 20 October 2026).
- The four existing ChatGPT schedules exist, but they previously lacked access to this repo and notifications were disabled; their private GitHub handoff **must be tested in actual scheduled execution**.
- The hourly GitHub price-only watcher ran with ZERO approved ideas on the initial test, so delivery of a nontrivial trade-triggered GitHub Issue has **not** been validated.
- GitHub Actions schedule timing is approximate, best-effort. Billing minutes can run out, even without using ChatGPT message limits.

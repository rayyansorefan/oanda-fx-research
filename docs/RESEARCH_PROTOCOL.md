# Research protocol: four full assessments plus hourly **news, macro and price** reassessments

**Purpose:** Discretionary guidance for manually placed Pepperstone FX spread bets. The hourly watcher automatically collects new economic announcements, FX market headlines and a macro calendar and compares them to selected setups. Its headline-based risk categories are heuristic flags, NOT an expert natural-language interpretation or a prediction of economic releases. No orders, Pepperstone access, Floot, paid APIs, or guarantees.

## Authoritative sources (private repository)

- **data/universe.json:** ALL OANDA-account currency instruments discovered at this run, with precise coverage counts, excluded/failed instruments and Pepperstone broker verification unknowns.
- **data/summary.json:** latest quotes, screened M15/H1 indicators for every successfully retrieved OANDA FX instrument, ranked research priorities, plus full M5/H4/D1 technicals on bounded shortlist. Priority is not a trade signal or success probability. Primary input to each scheduled ChatGPT briefing.
- **data/latest.json:** compact OANDA bid/ask/mid market snapshots on all successfully screened pairs, only 12 recent completed bars per available timeframe (adequate for short hourly checks, not a full intraday reconstruction).
- **data/pairs/{instrument}.json:** full saved time-ordered OHLC for up to 7 top-ranked and any currently watched ideas; check `data/universe.json` for actively collected `detailed_audit_pairs` before reading, to avoid stale shards. Without chronological bars do not claim reliable entry/fill reconstruction. Do not judge within-bar SL/TP ordering from OHLC.
- **signals.json:** up to three **previously researched** discretionary setup specifications (one instrument/direction per idea), prepared by the scheduled ChatGPT researcher when it has write access. Empty is correct if there are no defensible signals.
- **data/watch_state.json:** hourly watcher state to avoid duplicate issue alerts. Historical alerts are not filled trades.
- **data/news_latest.json:** timestamped official central-bank/statistical-release RSS, aggregated market headlines, free third-party economic calendar and per-currency relevance tags. Covers checked sources only, failures transparently shown.
- **data/hourly_assessment.json:** per-signal hourly news risk, release blackouts, lightweight technical checkpoints, OANDA price movements following detected releases and remaining risk/reward. These are observations, not news causality or human expert reassessments.
- **GitHub Issues [FX WATCH] and [FX NEWS REVIEW]:** price-pattern and headline/macro risk alerts. No immediate market-order recommendations, and Pepperstone spread remains unchecked.

Read using the connected GitHub plugin, repository **rayyansorefan/oanda-fx-research**. **Do not rely on search snippets, inferred links or the public web as a substitute for the authenticated private data.** Check the snapshot's \`status\`, \`collected_at\`, every quote \`time\` and \`tradeable\`, and the candle's opening time with \`complete=true\`. The historical candle time reported under \`completed_at\` is the OPEN of the last completed bar, not its close. Never call a 33-minute-old hourly sample a current broker quote.

If authenticated repo read or data validation fails, state DATA UNAVAILABLE and withhold executable entries. The scheduled ChatGPT task and GitHub Actions workflow are independent; an enabled schedule is not proof of successful source access.

## Four complete reports, UK time

- 07:30: screen the full available OANDA account FX universe on M15/H1, then analyse strongest filtered candidates and approved ideas on D1/H4/H1/M15 (M5 timing); rank 0-3 actual opportunities across D1/H4/H1/M15 (M5 confirmation only). Independently verify economic releases and major breaking news. Rank 0–3 technically and fundamentally supported ideas. For each, A breakout-and-retest, B pullback-rejection or C confirmed-momentum continuation route **only if individually justified**, exact conditional trigger, stop, TP1/TP2, cost-aware R:R, expiry, macro blackouts, disqualifiers and confidence limitations. Keep unqualified ideas out.
- 12:00: preserve immutable 07:30 recommendations, reconstruct candle paths, distinguish alert touching from actually confirmed and realistically executable fills, audit false/missed trades; recheck macro and revise/revoke only forward from the new timestamp.
- US pre-open (14:00 Europe/London fixed UK time, not a NY-clock shift): news actual-vs-consensus, U.S. yields, risk sentiment, USD, bank statements; update opportunity candidates, reject stale/small-R:R entries, and publish any valid additions/replacements in the watchlist.
- 16:30: audit original and revisions, current performance assumptions and near-term risk, decide KEEP/MODIFY/CANCEL/EXPIRED; retain both winning and losing/unfilled evidence, with no hindsight entry reconstruction as actual P&L.

All reports review the market-wide screen and then apply full deep analysis to the strongest possible Pepperstone-manual-execution candidates. More instruments does NOT increase the recommended trade quota. Reports analyse: price market structure, previous day high/low, H4/H1 trend and M15 candles, independent EMA20/50/200 alignment, RSI14, RSI pivot divergence candidates, MACD(12,26,9), ADX14/+DI/−DI, ATR14, Bollinger20/2, Stochastic14/3, confirmed swing highs/lows, Fibonacci 0.382/0.5/0.618 anchored only to verifiable swings, 20-bar support/resistance, pivots, candles and liquidity. No single indicator decides the trade. Avoid interpreting FX broker tick-volume as consolidated volume. Prioritize directional, regime, entry-time, volatility and macro agreement.

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

**The above is purely a structural schema example containing invented prices; it is NEVER a recommendation or instruction to put a trade on.** The \`created_at\` field must represent the genuine issue time (not retroactive). Use UTC correctly with UK daylight saving. If a scenario is unsupported, omit its \`alert_a\`, \`alert_b\` or \`alert_c\` rather than populating an arbitrary level. Route C requires strong M15 momentum and M15/H1 ADX and DI agreement, a fresh quote beyond the level and adequate remaining net reward; it is never an automatic order.

The hourly watcher now checks relevant freshly published RSS headlines, a public weekly economic calendar, new release blackouts, and OANDA technical/price effects on each already-approved setup. It flags new material news as REASSESS_NEWS_FIRST and withholds price-only alerts if the pair's required central-bank feed, market headline aggregation or calendar cannot be checked. Unavailable optional sources (e.g. BLS rejects GitHub with HTTP errors) remain transparently listed under LIMITED; qualifying price checks may proceed only with an explicit coverage caveat. It cannot independently authenticate the truth or implications of every headline, check the full content of every source, guarantee 100% global-news coverage, or check Pepperstone's executable price. Its alert is **REASSESS NOW**; not BUY NOW or SELL NOW. It screens historical M15 crossings but refuses if the quote is stale, planned TP1 already reached, planned stop passed, or remaining reward relative to risk inadequate. Hourly execution can be delayed and an old event can be over by the time the issue arrives.


**Manual-only signal types (23 October 2026):** A = confirmed breakout followed by a later M15 retest; B = M15 rejection and reclaim; C = strong completed M15 breakout/continuation without waiting for an ideal retest. C must show momentum candle quality, M15 and H1 trend-strength/direction agreement, fresh price holding beyond the level, and a viable after-cost stop/TP. The GitHub alert means REASSESS FOR A POSSIBLE MANUAL PEPPERSTONE ENTRY, not that the broker price is checked or a trade was placed. The four full ChatGPT briefs must still research and publish top directional scenarios, clearly distinguishing qualified conditional ideas from unqualified paper watches. Absence of authenticated Pepperstone prices does not prevent publishing an OANDA-based *conditional* research idea, but the user must check Pepperstone bid/ask/spread/symbol before acting.

## Execution and forward learning

Never open/close trades automatically. The user makes the final Pepperstone trade decision. Indicative risk <=0.25% of verified current account equity per idea; no invented balance or £/point size. The three positions per day is an aspiration, not a quota. Avoid three highly correlated USD exposures, stop inflation and trades into high-impact news. Headlines are not predictable; scenario analysis is different from claiming foreknowledge of releases.

Journal prospectively: timestamp, instrument, scenario A, B or C, planned entry/SL/TP, OANDA alert and candle close, actual Pepperstone entry/exit only if supplied, spreads, outcomes in R net of known costs, exclusions, expiry and misses. Analyze missed opportunities and false entries symmetrically with wins. Overfitting 1–2 days of market data is not evidence of profitable strategy.

## What is verified / what is NOT

- OANDA authenticated live practice-account data retrieval passed its initial test (GitHub Actions run 39486937460, 20 October 2026).
- The four existing ChatGPT schedules exist, but they previously lacked access to this repo and notifications were disabled; their private GitHub handoff **must be tested in actual scheduled execution**.
- The initial hourly GitHub watcher ran with ZERO approved ideas. Automated issue delivery for a true technical or news trigger needs a separate qualified live setup; it has not been proven until an actual such alert is sent.
- After adding news, verify the revised hourly workflow actually fetches multiple official RSS headlines and economic-calendar data and updates both news_latest.json and hourly_assessment.json, not just OANDA candles. An offline unit test or green setup alone is insufficient.
- GitHub Actions schedule timing is approximate, best-effort. Billing minutes can run out, even without using ChatGPT message limits.


## Full Pepperstone universe — verification gap

Pepperstone UK's website advertises 90+ FX pairs, but a general marketing count is not an authenticated list of symbols in this user's spread-betting account. OANDA's `/accounts/{id}/instruments` returns the user's OANDA-account **currency** instruments only. Different regulated brokers do not necessarily support matching exotics, NDFs or indices. Every scanned OANDA symbol is marked **Pepperstone availability UNVERIFIED** until the actual broker spread-betting instrument catalogue is authenticated. An unsupported symbol must not be recommended as an immediately executable Pepperstone trade. Most quoteable FX pairs are suitable for research screening, but emerging-market currency macro surveillance may have missing official feeds; label **news coverage unavailable** and withhold price-only alerts when necessary. Exotic pip values come from OANDA `pipLocation`, never universal 0.0001 assumptions.

Full deep technical readings are computed and stored for a bounded shortlist so the free hourly run does not inflate cloud minutes, data files or ChatGPT usage. A failure for one exotic pair is marked PARTIAL coverage, not silently interpreted as all 90+ Pepperstone markets passing screening. The four ChatGPT assessments should report exact counts of OANDA-discovered, M15/H1-screened, and deeply analysed pairs, plus exclusions.

## Mandatory pre-brief recovery when GitHub cron has missed the refresh

The ChatGPT full assessments (07:30 / 12:00 / US pre-open / 16:30 UK) must NOT treat stale market quotes as current. At the very beginning of each assessment:
1. Read authenticated \`data/summary.json\` and \`data/universe.json\` from this private repository. Compare \`collected_at\` and the actual \`quote.time\` against the current UK-local / UTC clock. Check recent GitHub Actions run history if available.
2. If no reliable market snapshot has been saved within the last **25 minutes**, launch **one** read-only refresh through the existing *GitHub push-triggered workflow*. Read \`scripts/refresh_request.json\` with GitHub \`fetch_file\`, change \`session_day_utc\`, \`sequence\` (increment integer), \`requested_at_utc\`, and \`request\` to identify THIS single assessment, then call GitHub \`update_file\` with the exact current SHA and branch \`main\`. Updating that file triggers the existing workflow because the existing \`push\` path filter includes \`scripts/**\`. **Never put API keys, account IDs, trading orders or client documents in the file.**
3. Confirm via GitHub Actions run history that a NEW push-triggered run actually started. When available, check the job result and freshly written \`data/summary.json\`, \`data/universe.json\`, \`data/news_latest.json\`. If it has not completed, recheck briefly during that task while time permits. DO NOT count committing the file as proof of successful data collection. If permissions or scheduling prevent fetching fresh data, explicitly report \`DATA UNAVAILABLE / WATCHLIST NOT UPDATED\` instead of inventing quotes.
4. Only after validating fresh timestamps, actual completed candles and news/currency coverage may the full trade-idea selection proceed, and only with an actual broker-executable quote check before any proposed immediate market entry.
5. This push fallback **does not itself create a fifth ChatGPT task or consume extra ChatGPT scheduled notifications**, but it does consume GitHub Actions minutes. Use it at most once per research assessment when stale. Do not start additional loops or repeated paid services. If the hourly GitHub schedule eventually resumes, skip fallback.
6. GitHub's \`schedule\` event on new private repos is best effort and so far **unverified** here: the 2026-10-21 06:27Z unattended attempt never ran. Push-triggered snapshots work when explicitly requested. Notification preferences for ChatGPT tasks are separate from data collection and must not be reported as fixed merely because OANDA data has refreshed.

The watchlist \`signals.json\` must only be changed with verified justified prospective monitoring plans; do not fill with fabricated placeholder trades to test delivery.

## REQUIRED signals.json schema and non-negotiable admission gate

Scheduled ChatGPT researchers MUST use these exact machine-readable keys. A field named `stop_loss`, `take_profit_1`, `take_profit_2` or `max_spread_pips` is **NOT** an accepted substitute:

```json
{
  "schema_version": 1,
  "signals": [{
    "id": "immutable-safe-ID",
    "instrument": "GBP_USD",
    "direction": "LONG",
    "created_at": "2026-10-21T08:00:00Z",
    "expires_at": "2026-10-21T12:00:00Z",
    "planned_entry": 1.3380,
    "stop": 1.3350,
    "tp1": 1.3440,
    "tp2": 1.3480,
    "alert_a": 1.3390,
    "alert_b": null,
    "alert_c": null,
    "max_oanda_spread_pips": 1.3,
    "event_blackouts_utc": []
  }]
}
```

The numbers above are a **NON-LIVE STRUCTURAL EXAMPLE ONLY**, not a trading signal. Candidate ideas MUST independently satisfy original watcher gates before handoff: (1) correct directional stop/entry/TP1, (2) gross TP1 R:R **at least 1.50**, (3) (TP1 profit minus allowed spread)/(planned stop risk plus allowed spread) **at least 1.35** using the **maximum permitted OANDA spread cap** and correct pipLocation, (4) at least one evidence-justified A/B/C route, (5) true UTC creation and expiry, (6) checked release blackout windows, (7) no ineligible Pepperstone product or stale data. A task may present a speculative chart thesis but it MUST NOT populate signals.json with a noncompliant idea. Each unqualified idea is invalidated individually with a visible reason; watcher never treats it as approved. Do not weaken the original risk thresholds to manufacture opportunities.

On 2026-10-21 a scheduled ChatGPT report wrote two improperly named ideas (EUR_GBP SHORT and GBP_USD LONG). Both also failed original cost-aware TP1 R:R gates. Exact original terms and calculated rejection reasons are preserved privately in `data/rejected_watchlist_20261021.json`; active list was cleared prospectively rather than retroactively editing history. Zero active ideas means price monitoring of qualified trades cannot yet generate entry alerts.

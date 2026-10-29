# Entry pipeline repair — 29 October 2026

These changes repair observability and timing defects. They do not establish profitable trade identification and do not relax champion admission or risk rules.

## Confirmed defects

- `oanda_data.collect`: only the ranked/prequalified subset received M5/H4/D. The 07:13:13 UTC snapshot had 68 M15/H1 screens but 19 complete deep charts. Four-frame filter effects cannot be assessed where H4/D context is absent. Full core collection now covers every successfully screened pair, with explicit individual timeframe failures. Prices are refreshed after chart collection.
- `signal_admission.admit` mutated its input archive in place. The subsequent comparison in `main` of returned archive versus original therefore never detected newly expired plans. Copying the archive restores persistence and preserves original immutable levels.
- `export_public_dashboard.main` used only hourly watcher evidence for canonical signal status, relegating fast checks to a separate field. A coherent fresh fast check now supplies the canonical status and refreshed per-pair prices/charts. Stale, future or mismatched streams cannot replace hourly evidence. The all-universe collection clock remains unchanged. No HTML/CSS/layout changes.
- `strategy_comparison.update` shared observation times across pairs. A fast scan could give an omitted pair an artificial early detection clock. New cohorts use actual pair-specific scan times (method 3). Prior records remain intact and are excluded from the new holdout.
- `forward_validation.evaluate` omitted missing cost scenarios from daily sums, potentially treating the exposure as a zero-return abstention. Statistical comparison now requires computable scenarios for every scored observation.

## Study preservation

The v4 protocol is archived in `docs/validation_protocols/`. V6 begins at the future boundary in `validation_protocol.json`; previous records/receipts and superseded lock remain preserved. Pre-v6 observations cannot support a v6 claim. Changed rule hashes invalidate the protocol. No forward success is claimed from these repairs.

## Remaining constraints in the 07:13 snapshot

Unmodified saved data gave five economically feasible route observations, not trades. EUR/USD and GBP/USD failed M15 ADX and news review. USD/CAD failed daily trend/DMI/reversal/news. USD/SGD lacked H4/D, had weak/opposing H1 and missing news sources. USD/JPY had several weak/opposing technical conditions and news review. Only 3/68 pairs passed news clearance. No threshold was waived.

The saved comparison had five pre-07:00 plans: two triggered-unfilled, two invalidated before entry, one pending. No scored champion/challenger holdout result or timely verified user receipt was established. These cannot be called missed profits.

## Verification and limits

254 offline tests passed locally, including a 68-pair collector integration, missing-frame/final-price failures, archive persistence, stale/mismatched fast-stream rejection, pair-specific timing, missing-cost handling and version migration. Actual collector output, public deployment and rendered dashboard still require post-commit checks; local tests do not prove those stages or strategy profitability.

No orders, live broker execution API, assumed equity, new schedules or paid services were introduced. Hourly/four weekday assessments, notification quiet hours and gross 1.50/net 1.35 gates are preserved. The existing fast workflow gains a deployment trigger; its cron windows remain unchanged. GitHub scheduling is best effort. Actual phone/provider receipt remains unverified.

## Real deployment findings and follow-up

The first real collector completed all 68 full core charts at 07:49:23 UTC. Export then failed its size ceiling; the persisted research was retained and the job reported failure. Reproduced using the saved public feed expanded to full core coverage. Added final compaction of unused pattern excerpts and repeated static metadata while preserving all quotes, displayed indicators, candles (minimum six), original plan levels and every indexed candidate. A realistic expanded-feed stress fixture dropped below 880 KB.

The first fast scan also reached export, but its workflow skipped persistence on export failure. The repaired existing fast workflow now persists even on failure, then reports failure explicitly. Replay deferral also preserves prior open/pending evidence rather than overwriting it with SOURCE_UNAVAILABLE. Known future high-impact event windows overlapping the plan life are now frozen into both live research and paper plans using the existing 45-minute buffers. Earlier immutable plans are untouched.

V5 was superseded before its holdout began. V6 retains the same 08:00 UTC start and all prior protocol locks/events. Full post-follow-up collector/fast/public-render verification is required before deployment is described as verified.

## Rendered verification and final overlay regression

The public deployment at commit 2f433f3 passed after updating its exact schema allowlists for fast monitoring and disclosed compaction (no layout change). Browser inspection verified 67/67 displayed pairs with deep coverage (GBP/ZAR excluded), 07:55 source data and the first completed fast scan. That inspection exposed deletion of the older M5/extended frames on pairs refreshed by the four-frame fast scanner. The overlay now merges frames, retaining their original candle clocks; a dedicated regression verifies M5/W preservation. V7 registers a new untouched 08:15 UTC boundary and preserves v6 and all earlier records.

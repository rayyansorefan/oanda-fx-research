# Entry and exit profitability work — 9 November 2026

## Evidence and rejected alternatives

The saved comparison has10 champion and47 challenger plans but no confirmed fills. The original exit-management study therefore has zero paired entries. The older paper ledger's four scored outcomes do not establish a predictive edge. Manual broker gains are not silently counted as model wins.

`research/experiments/entry_timing_probe.py` and `data/profitability_execution_development.json` reproduce the entry-timing investigation from existing actual OANDA bid/ask replay files. All distinct frozen plans are retained by ID and date, including rejected and unfilled cases. Development is before5 November 07:00 UTC; the later retrospective check is NOT a new untouched holdout. Case outcomes are not independent portfolio returns.

- Keeping the original future trigger and modelling a next-bar entry yielded no admissible development fills:4were rejected by actual fill economics and24had no confirmation.
- Entering unconfirmed plans at the next M15 open created9 development model fills, with a large negative aggregate after adverse slippage. It is rejected, not deployed.
- Adding a volatility-relative stop floor and bounded price deviation reduced losses in that small development sample. The later retrospective interval was still negative. The apparent development improvement is NOT a robust trading edge; no thresholds were retuned on that interval.
- Breakeven protection did not rescue the unconfirmed-entry hypothesis. Profit-protection is not a substitute for a sound entry.

The machine-readable report includes every case/result, missing coverage, exact cutoff, source ledger fingerprint and script fingerprint. It preserves raw losses and gaps. Reproduce with the commands in the script's `--help`. No GBP/account return is inferred from normalized model returns.

## New prospective candidate: completed impulse, cost-aware entry

`scripts/confirmed_momentum_execution.py` uses the existing frozen liquid-momentum detector: an ALREADY COMPLETED M15 impulse with H1/M15 trend agreement, H1 ADX >=18, body/close quality, spread/ATR cap, source/news freshness, and known-event protections. Registration is prospective, during 07:00–17:00 UTC weekdays, with a new separate identity. It does not enter an unconfirmed pullback or convert a directional watch into approval.

The three same-setup comparison arms are:

1. EXISTING_EXTRA_CONFIRMATION: the existing later-confirmation/observation/entry logic.
2. CONFIRMED_NEXT_OPEN: precommitted next-M15-open paper execution, using executable bid/ask, 0.25 pip adverse slippage each side and the ORIGINAL frozen stop/target. Reject chasing beyond 0.35 ATR, stop distance below 0.75 ATR, a stop inside the exit-side spread, excessive spread, or inadequate remaining gross/net reward. Never move a stop to manufacture eligibility.
3. CONFIRMED_NEXT_OPEN_PROTECTED: identical entry, with next-bar breakeven/trailing protection after +1 risk unit at a completed close, a next-open four-hour progress exit below +0.5 risk unit, and a 96-bar time cap. No same-bar hindsight tightening. An explicit extra cost/funding stress is recorded separately from actual OANDA bid/ask results; broker costs remain unverified.

All arms enforce at most 3 concurrent model positions, no duplicate pair, and at most 2 positions per currency. These are normalized comparisons at a planned 0.25% risk convention, not a claim to have verified account equity or sized a real stake. Caps use exposure at the entry instant, even when replay occurs later. Both stop/target touches stay ambiguous; adverse opening stop gaps are losses. Missing bars and API failures never erase a proven entry. Exact known weekend closures may be bridged; other gaps stay unresolved. Economic terms and code dependencies are fingerprinted and cannot change under an existing identity.

The new ledger is `data/confirmed_momentum_execution.json`. Registration start is its actual initialization time; old plans are not backfilled. Original v21, simple, contextual and liquid ledgers remain untouched. A modelled contingent-order fill is not evidence of a timely alert, broker fill or human trade.

## Automatic operation and limits

The existing full and backup-fast workflows run this comparison with their current data sources. Exit replay remains callable even when a new quote collection fails, so existing paper exposure can continue to be checked through historical candle GETs. New entries still require fresh valid source/news evidence. No extra task, broker mutation or paid model API is introduced.

The five FX tasks should read the new ledger and this document when discussing performance. Report the baseline, the two new arms, costs, paired sample size, unresolved cases, independent observation days, and every loss. Do not label a favorable development replay a proven profitable strategy, mix old cohorts, recommend real orders or claim an automatic promotion. Initial results are unvalidated. At least 100 independent pair-days across 20 London days plus positive stressed evidence and independent review are required even to consider promotion; sample floors alone do not prove profitability.

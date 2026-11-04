# FX engine repair — immutable baseline and preregistered acceptance (4 Nov 2026)

## Baseline recorded before code changes

- Engine main: `0e121ce65e1fd6e3a905000b90a860ad44b916a2`; public dashboard main: `b8af91617a808d1b5252f01ca4606bc1d3d1c415`. These SHAs are the recovery points; no historical ledger may be rewritten.
- Last successfully persisted OANDA summary source: **2026-11-03 11:55:41 UTC**, `status: OK`; 68 OANDA currency pairs collected and deep-analyzed. Actionable GBP/ZAR is excluded from public feed; OANDA availability does not establish Pepperstone tradability.
- 4 Nov 09:16 UTC hourly run (ID **41806153869**) failed in offline tests before collection. Fingerprint **registered 09f36ed3871572417ac0c2c8f46af253a449e46135cadecdec3bdba5ef577c55** versus **observed 12e77769f22f3b56c9a8118fa05c3c8cec2be00c42d4da1028e47d149d1c1578**. Collection, paper audit, admissions and publication were all skipped. This was an infrastructure/strategy-integrity outage, not evidence of no market signals.
- `data/paper_trade_ledger.json`: **1,190** candidate records (different from independent trades): **1 WIN_TP1, 2 LOSS_STOP**, 12 UNTRIGGERED, 1,172 UNTESTABLE, 2 INVALIDATED_PRE_ENTRY and 1 DATA_GAP. Three scored outcomes are too few to demonstrate edge; they must never be pooled with user-reported broker trades.
- `data/strategy_comparison.json`: **5 champion plans, 23 challenger_v1 plans, 0 fills in both arms**, 0 verified user deliveries and no net expectancy estimate. Records include a much larger pool of rejected route evaluations, which are not independent trades.
- `data/signal_admission_audit.json` from the last completed snapshot: 134 route evaluations, one still-listed conditional plan, zero newly admitted; overlapping rejections included reversal/exhaustion 75, invalid bid/ask/spread 37, four-frame trend conflict 16. This snapshot is historical.
- `data/exit_management_research.json`: **not committed** at baseline. Its absence is not a zero-loss result or a successful exit policy.
- `data/public_dashboard_status.json`: `PUBLISHED` for the **old 3 Nov snapshot**, not confirmation of 4 Nov market freshness. GitHub publication does not prove user notification receipt.

## Immediate implementation scope

1. Market collection and raw evidence persistence must proceed independently of failing strategy integrity tests.
2. Strategy admission, watch-trigger promotion, paper simulation and validation remain fail-closed on integrity failure.
3. A run-level assessment health record must bind the stage outcomes to the **original market-source time** and distinct assessment time. Publishing a revised health status cannot refresh the old market clock.
4. An unavailable assessment must publish `ASSESSMENT_UNAVAILABLE`, never `NO TRADE`, and must suppress all green/ready routes and stale plans.
5. The existing v17 fingerprint and prior data must be archived; a code-changing version is a **new prospective protocol**, never back-labelled v17 out-of-sample.

## Operational acceptance (decided before seeing new outcomes)

- Offline tests for success, failed validation with fresh quotes, missing/stale source, mismatch, duplicate publication, and no stale green remaining; no credentials or private account data in public output.
- GitHub Actions proves the collector actually runs on OANDA data even if tests fail, while downstream trading actions remain blocked.
- A successful **post-change** collector run produces an OANDA source time newer than 2026-11-03 11:55:41 UTC. The exact timestamp and publication status must be read back from `docs/fx-feed.json` in the public repo and from the deployed GitHub Pages URL; source time must not be substituted by `published_at`.
- Current job logs must show which stages succeeded/failed, with missed schedules/overrun acknowledged. A green CI build alone is insufficient.
- Repeated snapshot assessment is idempotent. Existing candidate and frozen-plan histories survive unchanged. Verify concurrent-write races against newer market timestamps and fail rather than overwrite newer data.
- The HTML page must clearly display unavailable/stale status, and never imply that a high-ranked watch has an approved trigger.
- No broker orders, newly configured external message channels, Pepperstone position reads, or paid services.

## Research acceptance (distinct and later)

- Prospective version freeze and UTC start date; prior development and pre-registration historical replays remain ineligible.
- At least **20 distinct trading days and 100 independent scored pair-days per arm** (earliest per pair/day) to review results. This is a *minimum evaluation threshold*, not proof of a profitable strategy. Review cost assumptions, data gaps, correlated exposure, unresolved trades and verified user-delivery coverage before promotion.
- Compare champion, simpler continuation/pullback challenger, and a predeclared straightforward benchmark at identical decision times on completed candles, with bid/ask fills, manual-delay stresses, slippage cases 0, 0.25 and 0.5 pips each way, nonzero/unknown commission and financing explicitly classified.
- Report gross and net R expectancy, max drawdown in R, signals/day, fills/day, expiry and nonfill rates, losing and ambiguous outcomes, plus day-clustered 95% uncertainty intervals. Reject unsupported conclusions from wide intervals.
- Test blockers using independent and overlapping counts, and compare marginal after-cost outcomes before changing filter policy. Do not loosen market-data integrity, event-risk or risk-cap safeguards to target an arbitrary trade count.
- **Pass candidate** only if evidence is complete, cost-stressed positive net expectancy and predeclared uncertainty rule pass, no severe risk regression, and independently verified prospective signals consistently arrive before realistic manual entry. Otherwise record **FAILED** or **INCONCLUSIVE**. No automatic strategy promotion.
- Entry target of ~2–4 credible opportunities per day is separately measured for feasibility, not imposed as a quota.

## Limits explicitly not represented as completed work

A single repair cannot manufacture 20 days of forward observations, reliable GitHub Actions five-minute scheduling, actual Pepperstone spreads, or verified ChatGPT user-delivery receipts. Existing 15-minute fast checks during limited UK sessions are best effort, not a five-minute guarantee. Refrain from declaring an operational restoration until both collection and deployed readback are verified.

# Five-task FX research operations — 9 November 2026

This is the current GitHub engine: `rayyansorefan/oanda-fx-research`. It is independent of the older Sites OANDA FX Lab. Do not call Sites, Floot, crypto/bookmaker tools, broker order endpoints, or change that older engine. User selected research and simulated trades only.

Five existing active tasks are reused in place, each once per hour, Europe/London, weekdays. They have distinct duties; this is not five copies of a scanner or a promise of 12-minute price updates. GitHub's existing full/fast collectors provide market observations independently, with best-effort timing. The registered rule lock, existing cohorts, raw losses, expiry rules, and immutable plans remain authoritative.

| Role | Minute | Owned work | Record |
| --- | --- | --- | --- |
| macro_events | :05 | Current official release/event research, conditional scenarios, coverage gaps | data/task_runs/macro_events.json |
| market_scan | :15 | All-pair screening, one stale-source recovery, concise decision summary | data/task_runs/market_scan.json |
| position_monitor | :30 | Paper entry/exit chronology and existing exit-management comparison | data/task_runs/position_monitor.json |
| candidate_review | :40 | Independent evidence challenge and prospective paper-watch registration | data/task_runs/candidate_review.json |
| evidence_audit | :50 | Balanced forward samples, lifecycle reconciliation, public serving and role health | data/task_runs/evidence_audit.json |

## Common contract

1. Read this document, `config/scheduled_research_roles.json`, current `validation_protocol.json`, and relevant current source code. Current registered admission code takes precedence over obsolete prompt-era v12/v20 descriptions. Do not alter locked rules or thresholds, inflate risk, reset evidence, promote a strategy, or force a minimum trade count.
2. Fetch files through the connected GitHub tools or a current checkout; do not rely on search snippets. Some GitHub fetch_file responses for large files contain empty content despite a nonempty SHA: obtain the actual blob or current raw/clone contents; an empty response is not an empty market. Inspect only current manifest-authorized pair shards.
3. Distinguish last source observation, failed collection attempt, signal creation, later completed trigger, later executable-side paper fill, scored exit, publication readback and actual notification receipt. A fresh fast quote covers only that pair. No source clock can be replaced with task time. Full research requires quotes at most25min old; within active London reviews request recovery at20min, never treat the outer70min limit as current execution evidence. Outside market sessions show closure/last-known data honestly.
4. Preserve locked champion, challengers, early/simple/liquid cohorts and user-reported broker positions separately. No user-reported gain becomes a model win; no untriggered favorable movement becomes profit. Costs, gaps, ambiguous bars, unfilled/expired plans, adverse outcomes and omitted pairs count in coverage.
5. Never submit or modify real or practice broker orders. Existing profit-protection analysis is a simulated comparison with the fixed-stop/target baseline, not a promise of a good exit. Preserve exact existing next-bar management, gap, stop/target ordering and costs. No retroactive trailing stop or cherry-picked exit.
6. No credentials or broker/account/client details in any file or output. Public dashboard updates use only the existing validated exporter schema. Public serving, repository publication and task invocation require separate evidence.
7. Only market_scan retains the existing concise daytime decision report (3–5 short emoji bullets, no displayed R multiples). Distinguish SIMULATED ENTRY/EXIT, CONDITIONAL PAPER PLAN, UNTRIGGERED WATCH and WAIT. Do not recommend real orders. No routine user output23:00–07:00 Europe/London. Other four jobs persist quietly. No new tasks or unrelated notifications.

## Recovery ownership

Market_scan owns at most one full stale-data request per review. Read `scripts/refresh_request.json` and its exact current blob SHA on main, increment sequence, stamp genuine UTC request time/date and this role. Preserve other fields. Write with the exact SHA, then verify the specific resulting current-main Actions run and source/admission/publication results. Coalesce a request/run already in progress. A commit is not successful collection. Observe completion for at most three minutes, using short nonblocking checks; pending stays pending. No blind rerun of obsolete code.

position_monitor may request one existing bounded fast recovery ONLY when actually open or prospectively pending paper plans lack fresh pair-specific observations. Use the same supported refresh file, exact SHA and current main, with commit marker `[fx-fast-check]`, which selects the existing fast-tick job. This is an operator-triggered recovery, never an independently scheduled dispatch. Do not use it as another whole-market scanner. Coalesce in-flight work and retain unknown exits if recovery fails. Other roles do not request duplicate scans.

OANDA may succeed in this GitHub engine even when older Site credentials fail; never transplant the Site's401 diagnosis here. Validate actual current collection_attempt and fast evidence instead. Macro-source errors and calendar uncertainty remain visible, never invented clearance.

## Per-role completion receipts

Each role updates ONLY its own existing `data/task_runs/<role>.json`, using GitHub `fetch_file`/`update_file` with exact current SHA and readback. Do not write another role's receipt. Allowed fields are in the registry. `started_at`/`completed_at` are actual execution instants; `source_at` is the observed source clock, not completion. `source_commit` is the actual source revision inspected. Record actual counts, source paths/URLs, blockers, and verified Actions run IDs. `no_broker_orders` must be true. COMPLETE requires every promised duty/readback; partial or source failure is PARTIAL/BLOCKED/FAILED. No backfill of scheduled occurrences and no fabricated notification receipt. Never overwrite old market/plan clocks to make the receipt appear healthy.

The pipeline's `scripts/scheduled_task_audit.py` writes `data/scheduled_task_health.json` from these receipts and source clocks. It never marks a scheduled task complete on its behalf. A local verification is not an actual scheduled run.

### macro_events

Read currencies in current frozen plans, paper positions, and leading candidates. Independently inspect relevant official central-bank/statistical release calendars and announcements (next48h), publication dates, explicit timezones, rate/inflation/jobs/growth developments, credible reporting and missing coverage. Record verified facts and URLs, conditional scenarios and uncertainties in findings. Do not claim every source checked or guess actual/consensus/rates. Do not manually overwrite machine news_latest or clear a hard blackout; other roles use the findings as additional research context.

### market_scan

Read universe, actual summary/latest, collection_attempt, fast audit, monitoring_health, current protocol, signals, admission audit, hourly assessment and macro receipt. Screen all actually quoted pairs, disclose measured exclusions; deeply assess the bounded top12 plus existing monitored ideas with only available D/H4/H1/M15/M5 context. Read canonical current admission and source/news checks; do not make indicator votes or win probabilities. Reconcile candidate source, admission, publisher and actual served feed. Use the existing exporter rather than hand-copy a new permissive feed. Report0–3 genuine prospective paper plans or an honest WAIT; no invented levels and no forcing entries. Persist current counts/rejection causes and publication evidence.

### position_monitor

Read current paper_trade_ledger, strategy_comparison, early/simple/liquid cohorts, lifecycle ledger, exit_management_research and needed actual bid/ask chronological bars. Prioritize every currently open simulated position and pending intent, not just winners. Verify later triggers, separate later fills, stop/target/expiry/time exits, gaps and the fixed-vs-profit-protection study. Use existing automated scorer as authority; never manually relabel a result. Compare original entry, current exit-side price/time, stop/target and source freshness; hypothetical movement stays hypothetical. Machine scorer remains responsible for ledger updates. Record all open/pending counts, unresolved coverage and newly verified simulated exits in this role's receipt.

### candidate_review

Read current full and pair-specific fast source, macro and market findings, actual admission reasons and strongest prospective watches. Independently challenge price structure, regime, true completed trigger, news/spread/correlation, stop/target economics and expiry. Preserve registered gross>=1.50 and spread-adjusted>=1.35 gates and all current code controls; existing registered policy is authoritative. Inspect signal_plan_archive and all historical IDs before additions. Append only genuinely new prospectively timestamped research candidates through the current validated paper-trade-candidates schema, exact SHA and readback. Canonical signals.json stays owned by the existing admission pipeline; never overwrite it to manufacture approvals. Unsupported continuation replay remains untestable. Record accepted/rejected reasons and counter-evidence.

### evidence_audit

Reconcile complete registered cohort IDs against scored/unscored paper and lifecycle records, source coverage and public sanitized feed. Inspect current locked protocol/hash, forward_validation, early_forward_validation, simple/liquid metrics, exit-study matched cases and public serving. Wins, losses, unfilled, stopped, expired, ambiguous and missing cases all remain. Compare net-of-cost expectancy, drawdown and fixed-vs-profit-protection results on like-for-like cohorts; insufficient samples stay unvalidated. Read other four receipts, report stale/missing roles without fabricating their completion, and persist an exact retry context for operational faults. No automatic rule changes, strategy promotion, account reset, public raw-ledger export or claim of guaranteed profit.

## Setup verification — 9 November 2026

All five existing task IDs in the registry were updated in place and read back enabled. They run hourly weekdays at :05/:15/:30/:40/:50 Europe/London; no additional active task was created. First future role completions must still be established by real receipts, not this setup check.

451 local regression tests passed, GitHub CI run42708421618 passed, and the registered trading-rule digest is unchanged. Operations commits:33e6bfb9d116ef8d562c28801c17e8bb6697d8d6 and91dbfe79d41d251de7798c5908d2d9dcf51f6e9a.

Live source verification run42708419533 at22:04:28Z authenticated successfully:1accounts request,1instrument request and2pricing requests succeeded with no recorded HTTP failures. Pricing returned68records, all68 with tradeable=false, none missing bid/ask. The correct action is NO ENTRY / WAIT, not a claim that credentials are invalid or a relaxation of tradability. The last valid21:13:50Z source was retained with its original clock. This does not prove a fixed cause or duration for the provider's non-tradable state. Future workers must recheck actual new responses and distinguish fresh non-tradable quotes from stale usable observations.

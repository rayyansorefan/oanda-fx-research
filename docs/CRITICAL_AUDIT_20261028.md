# FX diagnostic, 28 October 2026

Status: code corrections tested offline; strategy profitability NOT validated.

## Confirmed defects and repairs

- `signal_admission.qualify`: absent H4/D indicators were labelled FOUR_FRAME_TREND_CONFLICT. Missing history now has its own reason. Per-pair/per-route rejections are retained, rather than only aggregate first-failure counts.
- `fx_universe.deep_selection`: more than 12 preliminarily qualified pairs could be excluded from mandatory H4/D verification. All preliminary qualifiers now receive deep selection. Arbitrary rank alone no longer removes them. This increases collection requests; workflow runtime must be monitored.
- `entry_readiness.entry_diagnostics`: thematic direction could disagree with the direction used for actual admission. Diagnostics now use the same scout direction and recompute exhaustion. Summary last-three-bar evidence is supported by `adaptive_research.entry_context`.
- `signal_watch.signal_match`: a candle opening before plan creation could trigger an apparently forward alert. Require a wholly post-registration candle and respect expiry. Recheck original gross 1.50 and net 1.35 gates at current entry side, replacing the weaker 1.35 gross-only recheck.
- Momentum admission allowed ADX 20 while watcher and paper replay required 22. Newly frozen plans explicitly carry the shared threshold and original indicator evidence. Legacy plans retain their original threshold. Current technical reassessment remains a separate safeguard; historical triggers do not borrow later indicator values.
- `signal_watch.setup_already_breached`: midpoint extremes and the candle forming before registration could incorrectly invalidate plans. Use completed, wholly post-registration bid exits for longs / ask exits for shorts; missing executable-side evidence fails closed.
- `oanda_data.collect` / `opportunity_tracker.main`: compact latest contained only 12 M5 bars (one hour), insufficient after delayed collectors. Every deep pair now has a full archive; tracker loads only matching-timestamp archives. This cannot provide real-time notifications between hourly polls.
- `missed_entry_audit.audit`: previous results were read from `results` although output uses `historical_results`, causing repeated replay and starving bounded work. Corrected and regression-tested. Prior audit is preserved separately when replay evidence version changes.
- Replay GET responses are now saved as content-addressed, credential-free OANDA bid/ask evidence. Terminal outcomes can be independently reproduced; old evidence was generally not retained.

## What the evidence does and does not show

Source commit: 2a21283fc4aa5ba7fe5e551d81c809c8e2993885.

28 October: 77 registered route records represent 74 distinct pair/direction/source-impulse groups. Seven pattern records represent six distinct pair/direction/pattern-time events. Original saved replay: two pre-entry invalidations, two same-fill-bar ambiguities (one duplicate USD/MXN event), one unfilled, one unresolved, one stop. Zero established profitable missed entries. These original outcomes require fresh source-evidence replay; they are not proof of alert-time fills.

Of 35 records with an ever-favourable directional move, six have recorded favourable observations inside their expiry window. Legacy incomplete history means absence of an in-window observation is not proof that no move occurred. Neither count is profit.

The stopped AUD/JPY hypothetical entry was at 21:45, exit 22:45, detection 22:49:49. Its 21:30 pattern was detected 79.82 minutes late. Six other pattern records have no recorded detection time. The replay must not be advertised as an actually available manual trade. New latency audit explicitly separates this evidence.

27 October: 67 registered route records, 63 distinct source-impulse groups, no saved forward entry patterns; in-window historical coverage incomplete. No verifiable profitable missed-entry total can be inferred.

Historical paper ledger: 600 candidates, 587 UNTESTABLE, 10 UNTRIGGERED, one WIN_TP1, one LOSS_STOP, one INVALIDATED_PRE_ENTRY. This is inadequate evidence for ranking profitability. Two terminal outcomes are not a calibrated strategy edge.

## Champion/challenger comparison

`data/strategy_development_audit.json` records eight immutable saved assessment snapshots near four assessment times on 27–28 October. Seven usable, one DATA_UNAVAILABLE. Both policies generated zero qualifying plans. No fill return, Sharpe ratio or drawdown can be established. This is development evidence, not out-of-sample validation.

Paper-only challenger V1 tests M15/H1 alignment and H1 ADX >=18 instead of mandatory four-frame alignment and both ADX >=20. Same available nearest-structure target methodology, stop geometry, spread rules, exhaustion check, economic calendar/news gates and original gross/net thresholds. It cannot write live signals or automatically promote itself.

`strategy_comparison.py` freezes both arms prospectively and replays bid/ask chronology against actual observed collector times, a one-minute manual delay and the next fully subsequent M15 open. Same-source-bar plans are deduplicated. Only 07:00–23:00 London observations can support a simulated manual response. Missing/ambiguous data is unscored. Net R uses spread-side fills/exits; financing, Pepperstone execution, slippage and correlated portfolio risk remain unmodelled. Drawdown in this study is per-trade R, not account equity drawdown.

Remaining strategies (reversals, M5 entries, breakout/retest variants) have no adequate independent validation and are not enabled as approved signals. No stake is inferred without verified account equity. No broker execution APIs, new schedules, paid services or dashboard HTML changes were introduced.

## Post-deployment verification

First repaired collector run 40786885272 completed successfully, source 23:23:08 UTC. Archive-backed pattern detection recovered older patterns previously omitted from compact histories. Updated 28 October totals: 79 registered records, 18 pattern records, 15 distinct events. Raw response hashes and all 18 replay statuses were independently recomputed locally: nine hypothetical stops, two pre-entry invalidations, two ambiguous fill records, three unfilled, two unresolved, zero TP1 wins. Distinct-event status groups: see `data/independent_replay_verification.json`. These are not 18 independent executed trades and many were detected hours late.

The existing public front panel called unapproved models current conditional plans and asserted full alignment even with conflicting D1. Public change 154ae1d only corrects text labels; layout is unchanged. Canonical conditional signals remain in the existing entry-decision section.

A further persistence defect kept `signals.json.generated_at` at 27 October when the signal list remained empty. Persist the new assessment envelope every cycle while preserving each original frozen plan. Regression added.

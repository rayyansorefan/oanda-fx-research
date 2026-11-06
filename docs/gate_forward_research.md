# FX-GATE-20261106-v1 — forward-only paper observation

**Scope:** Evidence collection only. This does not relax a production gate, approve trading, change the three registered forward protocols or access Pepperstone.

## Question

When a technically and economically feasible EMA20 pullback is rejected **solely** because a primary news source is missing, what happens to its future M15 trigger and its hypothetical executable-side price outcome?

This hypothesis is deliberately separate from cases rejected for a *known* material headline or event, and from routes with multiple failures (for example structural reward/risk and trend conflict).

## Registration and integrity

- Protocol: `docs/validation_protocols/FX-GATE-20261106-v1.json`; source: `data/admission_full_gate_audit.json` from the current full-market observation, not historical scans.
- No registration before 10:30 UTC on 6 November 2026. The first deployed invocation **only** activates the study. Hypotheses require a later full-market source timestamp, and a matching per-snapshot gate audit.
- Select rows with `failed == ["news_gate"]`, `news_reason == "PRIMARY_NEWS_SOURCE_MISSING"`, all other gates true and positive gross/net research economics.
- Freeze id, direction, source/decision time, pullback entry, stop, nearest target, 90-minute expiry, source-row hash and spread assumption before next candle.
- Cap at three new hypotheses per source, one per instrument/day and twelve active at a time. Counterfactuals are not alerts.
- Score only with frozen M15 bid/ask replay after creation, and actual subsequent pair-specific monitoring observations. Missing bars, delayed detection, trigger-without-fill, ambiguous same-bar excursions and OANDA outages are **not** counted as winning trades.
- Keep the original and result evidence under `data/gate_forward_paper.json`; persisted price evidence is kept private.

## Interpretation and later decisions

This deliberately explores **one** possible opportunity-limiting filter. A hypothetical winning paper record **cannot** retrospectively establish the missing news source was safe, that a broker spread matched OANDA, that a user received an alert, or that a discretionary trade was available.

Compare outcomes with non-missing-news control cases matched by currency exposure, session, trend/volatility and realistic response delay. Treat correlated pair/day outcomes as non-independent. Before any production policy amendment, independently review the missing-news risk, full event coverage, opportunity count, broker cost stress and untouched forward evidence. Register the altered policy under a **new** prospective protocol. Do not rewrite the current frozen protocol or replace the original records.

**Deployment verification:** PR tests, actual subsequent source registration, at least one subsequent observed M15 trigger/non-trigger, append-only persistence and cost-aware scored evidence. Until all observed, classify this as **RESEARCH IN PROGRESS**, not self-improvement proven.

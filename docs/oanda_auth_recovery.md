# OANDA read-only collection: production authentication recovery

This document is an operator action checklist. It does **not** claim a token was repaired or a fresh market observation was collected.

## What the collector now distinguishes

- `OANDA_INVALID_ENVIRONMENT`: repository variable is not exactly `practice` or `live`.
- `OANDA_TOKEN_MISSING`: token was not provided to the workflow.
- `OANDA_HTTP_401_ACCOUNTS`: the independent accounts endpoint rejected the request.
- `OANDA_TOKEN_REJECTED_PRACTICE` or `_LIVE`: after the configured account's instruments request was denied, the independent accounts check also rejected the token on that environment.
- `OANDA_ACCOUNT_NOT_ACCESSIBLE_PRACTICE` or `_LIVE`: token was accepted by accounts endpoint, but the configured account was absent from its accessible list.
- `OANDA_INSTRUMENTS_ACCESS_DENIED_...`: token/account accepted, but instruments endpoint denied the request.
- `OANDA_HTTP_401_PRICING`, `_CANDLES`: the failed stage, not a misleading "no quotes/no history" verdict.
- `OANDA_HTTP_504_...`: gateway failure with the existing bounded transient retry policy.

These errors are safe codes: no token, account ID, account-list response or full URL is written to public artifacts. Never retry 401 by switching practice/live automatically, and never downgrade it to a successful observation.

## One-time owner-controlled checks

1. In the private repository, check **Settings → Secrets and variables → Actions**. Confirm the `OANDA_API_TOKEN` secret exists and is current. Do not expose it in tickets, chat, PRs, or logs.
2. Set the **nonsecret repository variable** `OANDA_ENVIRONMENT` to `practice` or `live`, corresponding to the OANDA account/token. If absent, the code intentionally defaults to `practice` for compatibility. It never uses the live host silently.
3. Check the optional `OANDA_ACCOUNT_ID` secret belongs to the same environment and is accessible to that token. If deliberately omitted, auto-discovery requires exactly one accessible account.
4. Regenerate/re-enter credentials *only if the diagnostic evidence indicates rejection, expiry or mismatch*. Do not rotate a valid token simply because an upstream 504 occurred.
5. Run the private **OANDA FX read-only research data** workflow manually with `monitoring_mode=full`. Review the run, `data/collection_attempt.json`, and `data/summary.json`. An HTTP 204 dispatch acceptance is not success.
6. Require a fresh `COLLECTED` receipt, source timestamps newer than the request, valid pair-level quotes/completed candles, valid forward research, and public dashboard readback. A successful workflow or old retained snapshot alone is insufficient.
7. Independently configure the 15-minute scheduler as documented in [independent_fast_dispatch.md](independent_fast_dispatch.md). Four consecutive independent valid observations and two UK sessions are required before cadence/reliability claims.

**Scope:** No broker orders. Frozen strategies and their hashes are unchanged. Authentication recovery is a prerequisite for market research, not evidence of positive trading expectancy.

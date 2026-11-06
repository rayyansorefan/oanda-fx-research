# Frozen OANDA collection: 401 production diagnosis and owner recovery

This is an operator checklist, not a claim that credentials were repaired, market collection was successful or any trading edge was established.

## Integrity constraint

Existing prospective protocols hash `scripts/oanda_data.py` and the original `.github/workflows/oanda-fast-entry.yml`. Both files must remain **byte-identical** to the registered rules to preserve the three historical forward-study cohorts. They currently use OANDA **practice** (`api-fxpractice.oanda.com`) only. The operational observer reports the actual fixed host; it does **not** change host selection, account permissions, the collector, entry logic, original strategy digests or retry policy.

If the intended token/account is for OANDA live, **do not** silently reroute a frozen cohort. Make a separately registered prospective data-source/version transition after independent review, while retaining the original cohorts intact.

## Read-only diagnostic results

The operational wrapper `scripts/production_recovery.py` now captures which request *stage* encountered an API 401/403, without publishing an account ID, URL, token or HTTP response body:

- `OANDA_HTTP_401_ACCOUNTS`: authentication rejected at accounts discovery.
- `OANDA_HTTP_401_INSTRUMENTS`: denied at instruments; the wrapper performs one **different** GET `/v3/accounts` to isolate cause, never retrying the original denied request.
- `OANDA_TOKEN_REJECTED_PRACTICE`: second accounts call also rejected the credential.
- `OANDA_ACCOUNT_NOT_ACCESSIBLE_PRACTICE`: token accepted, configured account not on accessible list.
- `OANDA_INSTRUMENTS_ACCESS_DENIED_PRACTICE`: account accessible but instrument request denied.
- `OANDA_ACCOUNT_PROBE_INCONCLUSIVE`: a timeout/transient error prevented distinguishing the cause; do **not** claim invalid token.
- `OANDA_HTTP_401_PRICING` / `_CANDLES`: denied request was observed even if existing collector later returned generic missing quotes/history.
- `OANDA_TOKEN_MISSING` or `OANDA_ACCOUNT_CONFIGURATION_REQUIRED`: secrets/configuration absent or ambiguous.
- `OANDA_HTTP_504`: transient failure; original bounded retries and no-trade outage behavior are preserved.

No diagnostic automatically switches accounts or hosts. The original primary fast workflow uses the frozen collector without the new wrapper; its result must be independently assessed. The operational wrapper covers hourly full and dispatchable fast-backup jobs.

## One-time owner-controlled action

1. In the **private** repository go to **Settings → Secrets and variables → Actions**. Verify `OANDA_API_TOKEN` is present and current and is a **practice** token for the frozen source. Never post its value in chat, issues, commits, or workflow logs.
2. If `OANDA_ACCOUNT_ID` is configured, verify that it belongs to the same practice account and the token can read it. If unset, discovery requires exactly one available account.
3. Do **not** regenerate a token merely because an intermittent 401 appeared; inspect the newest `data/collection_attempt.json` receipt and the observed stage first. A successful subsequent run suggests intermittent or stage-specific failure rather than proven permanent expiry.
4. Trigger the private **OANDA FX read-only research data** workflow with `monitoring_mode=full` and inspect a **new** `COLLECTED` receipt, source timestamp, pair quote/candle evidence, downstream research and public dashboard readback. Workflow success or HTTP 204 alone proves nothing about market observations.
5. Configure the **independent** quarter-hour dispatcher as described in [independent_fast_dispatch.md](independent_fast_dispatch.md). Four consecutive separate events with valid new pair evidence and two full active UK sessions remain required to verify cadence/reliability.
6. Never promote a strategy solely because source collection recovered; validate prospective net outcomes and actual delivery independently.

**Security:** GET-only OANDA; no Pepperstone order access and no exposure of account IDs/secrets.

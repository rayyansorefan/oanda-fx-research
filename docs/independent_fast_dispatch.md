# Independent 15-minute FX dispatcher — operator handoff

## Why this exists

On 4 November 2026, the registered GitHub Actions 'schedule' events were
delayed or dropped for hours. One scheduled fast job exited successfully but
recorded `STALE_HOURLY_SOURCE` and made **no new market observation**.
A deployment, code tests, a 204 dispatch response, or a GitHub Actions success
state is **not** proof of observed-market freshness.

GitHub's native cron is retained as best-effort fallback, but the separate
15-minute dispatcher should independently issue authenticated
`workflow_dispatch` requests to the EXISTING private hourly workflow.

The new `monitoring_mode=fast` input runs **only** `fast-tick`, not the full
hourly `snapshot` job. It uses the existing OANDA GET-only collection,
bounded stale-source recovery, fresh-only fast scanner, paper research,
public-feed publication and no-broker-orders safeguards. If already fresh,
no duplicate market scan is requested.

## One-time operator setup required (not done by the repository)

**No third-party account or token has been configured by this PR.**
Use an independent scheduler such as [cron-job.org](https://cron-job.org/en/)
(free custom HTTP headers/method/body), or an equivalent trusted scheduler.

1. In GitHub create a **fine-grained personal access token** with access only
   to `rayyansorefan/oanda-fx-research`, with repository **Actions: Read and
   write** permission; Metadata: Read is automatic. Do not grant Contents
   write, administration access or a classic broad `repo` scope.
2. Store the token **only** in the chosen scheduler's protected HTTP header;
   never paste it in ChatGPT, commit it to GitHub, embed it in a public web
   page or include it in issue logs. Protect the scheduler account with MFA.
   This is a third-party credential trust decision the repository cannot make.
3. Configure an HTTP request:

   - Method: `POST`
   - URL: `https://api.github.com/repos/rayyansorefan/oanda-fx-research/actions/workflows/oanda-hourly-research.yml/dispatches`
   - Header `Authorization: Bearer <fine-grained-token>` (**replace locally**)
   - Header `Accept: application/vnd.github+json`
   - Header `Content-Type: application/json`
   - Header `X-GitHub-Api-Version: 2022-11-28`
   - Body: `{"ref":"main","inputs":{"monitoring_mode":"fast"}}`

4. Set timezone `Europe/London`, weekdays Monday–Friday, four executions
   per hour between 07:00 and 22:59. Suggested offsets are :06, :21, :36
   and :51. Avoid starting on minute 00, and keep the existing GitHub cron
   enabled as a backup. A second call close to a successful primary scan
   normally exits without another OANDA request.
5. Inspect the scheduler's execution history. GitHub API HTTP `204` means
   the dispatch was **accepted**, not that the collector ran or succeeded.
   Confirm `event:workflow_dispatch`, `monitoring_mode=fast`,
   `fast-tick conclusion=success`, `snapshot=skipped`, actual
   `data/fast_entry_audit.json status=FAST_RESEARCH_CHECK_COMPLETE`
   with real `source_at`, `data/fast_latest.json` source equality,
   timestamped `data/fast_entry_history.json`, and public feed readback.
6. Verify at least four **consecutive independent dispatches** and fresh
   pair-specific observations at the intended interval. Preserve their
   run IDs, actual clock times, failures and gaps. Compare with GitHub's
   separate `schedule` event results. No false delivery receipts.
7. Set **GitHub Actions spending/billing limit to zero excess spend** before
   enabling frequent dispatches. A private repository consumes included
   Actions minutes; 15-minute weekdays can materially increase usage.
   If minutes run out, pause rather than incur a charge.
8. Rotate the token before it expires, revoke it if the scheduler is
   compromised, and disable the external schedule immediately if data
   persistence or market freshness fails repeatedly.

**Security:** Read-only OANDA research only; this dispatch cannot place
Pepperstone trades. Independent provider scheduling still has delays/failure
modes; claim *observed reliability*, never an absolute SLA. Never promote
paper strategies based only on increased observation count.

## Verification statuses

- `READY_FOR_DISPATCH`: merged code, CI passed and manual fast-input
  dispatch passed (not proof of unattended scheduling).
- `LIVE_RESEARCH_SEEN`: one authenticated external event produced a valid
  source observation and public readback (not continuity).
- `CADENCE_OBSERVED`: >=4 consecutive independent dispatcher events
  produced valid new observations, with intervals measured and no hidden gaps.
- `DEGRADED`: scheduled requests rejected, unsuccessful/missing dispatches,
  stale observations or publication mismatch.

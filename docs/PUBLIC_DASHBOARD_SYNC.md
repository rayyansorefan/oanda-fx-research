# Automatic public dashboard synchronisation

The FX HTML site is PUBLIC. The OANDA collector in this repository is PRIVATE. The exporter `scripts/export_public_dashboard.py` creates an explicit whitelist of safe quote/candle/news/signal fields and never exports OANDA API credentials or account IDs.

## One-time owner setup required
Create a fine-grained GitHub token with access limited **only** to `rayyansorefan/standalone-wallet-paper-agent`, with Repository permissions **Contents: Read and write**. Store it as the Actions secret `FX_DASHBOARD_DEPLOY_TOKEN` in this PRIVATE `rayyansorefan/oanda-fx-research` repository (Settings → Secrets and variables → Actions). Never place the token in the public repo, a webpage or chat.

The private OANDA workflow will then, after a completed OANDA + news collection, automatically update `docs/fx-feed.json` in the separate public repo and trigger GitHub Pages deployment. If the secret is missing, the publisher records `NOT_CONFIGURED` and does not claim success. See `data/public_dashboard_status.json` for results. A failed publish is explicit and the webpage blocks entries when its feed becomes stale.

**GitHub Actions cron remains unverified:** a secure publishing bridge does NOT fix an unreliable GitHub hourly scheduler. Verify a real `event=schedule` run, or use a separately approved external trigger. The public site itself polls published data each minute but does NOT make new OANDA requests. No auto trading or Pepperstone execution.

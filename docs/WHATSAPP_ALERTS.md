# Private WhatsApp alert system

This automatic private GitHub Actions step runs after every successful research/technical/news watcher run and delivers **only qualified setup reassessments**, key macro risk/blackout warnings and invalidations. It never places trades.

## Required one-time Meta business setup

Use a WhatsApp Business Platform sender, opt in your intended recipient, and create a Meta-approved outbound message template named `fx_research_alert` in language `en_GB`. Its body needs exactly three placeholders, e.g.:

FX research notification: {{1}}
Details: {{2}}
Research charts: {{3}}
For information only. Confirm entry and Pepperstone price manually.

In the **private** GitHub repo `rayyansorefan/oanda-fx-research` (Settings → Secrets and variables → Actions) add four encrypted secrets:

- `FX_WA_ACCESS_TOKEN`: Meta WhatsApp Business Cloud API access token with messaging permissions.
- `FX_WA_PHONE_NUMBER_ID`: sender's numeric Meta WhatsApp phone-number ID.
- `FX_WA_RECIPIENT`: opted-in recipient's UK mobile in international digits, **without plus or local leading zero**.
- `FX_WA_TEMPLATE_NAME`: the exact approved template name, `fx_research_alert`.

Optional `FX_WA_LANGUAGE` defaults to `en_GB`.

Never put phone numbers, Meta tokens or sender IDs in HTML, public repositories, chat reports, GitHub issues or logs. Only the intended recipient and sender details belong in encrypted private GitHub Actions secrets.

## Controls and limitations

- Respects **23:00–07:00 UK** quiet hours including BST/GMT transitions.
- Positive alerts require previously approved original setup, fresh complete watcher and 5-minute OANDA quote, verified M15 route, gross TP1 R:R >= 1.5, capped-spread-adjusted >= 1.35, valid expiry and no blackout. Every potential-entry message says to confirm live Pepperstone bid/ask before manual action.
- Meta's WhatsApp policy requires an approved message template for outbound business-initiated messaging outside the service window. Meta may charge per message and requires opt-in.
- Deduplication state is stored privately at `data/whatsapp_alert_state.json`. Delivery audit is `data/whatsapp_delivery_status.json`; `ACCEPTED_BY_META` only proves acceptance, not phone delivery (which needs verified webhooks).
- With zero qualifying signals, `NO_ALERTS` is expected; not proof of successful delivery. Without configured secrets, a qualifying alert reports `NOT_CONFIGURED`; do not assert alerts are operational.
- Public dashboard feed auto-synchronization has a **separate** `FX_DASHBOARD_DEPLOY_TOKEN` prerequisite; adding WhatsApp keys does not resolve it.

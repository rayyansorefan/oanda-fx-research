# 2026-11-04 operational baseline (pre-repair)

Observation: 2026-11-04 09:47-09:55 UTC. The following are source-specific observations, not simulated performance.

- Private engine main SHA: `0e121ce65e1fd6e3a905000b90a860ad44b916a2`. Public dashboard main SHA: `b8af91617a808d1b5252f01ca4606bc1d3d1c415`.
- Hourly workflow run https://github.com/rayyansorefan/oanda-fx-research/actions/runs/41806153869 failed at offline tests. 296 tests, one failing `test_live_rule_digest_matches_registered_holdout`: registered 09f36ed3871572417ac0c2c8f46af253a449e46135cadecdec3bdba5ef577c55 vs current 12e77769f22f3b56c9a8118fa05c3c8cec2be00c42d4da1028e47d149d1c1578. Collection, paper, admission, export all skipped.
- Last private publication status: `PUBLISHED` for source 2026-11-03T11:55:41Z, read back 2026-11-03T14:54:36Z; this is not a fresh 4 November market observation.
- Comparison `data/forward_validation.json`: champion 0 independent scored pair-days, challenger_v1 0; status PROTOCOL_CHANGED, protocol FX-OOS-20261102-v17. The existing comparison and ledger JSON are too large for this connector's content reader and must be counted separately by the running jobs; no invented totals.
- `data/signal_delivery_audit.json`: 4 recorded conditional-plan events, 0 independently verified user-delivery receipts. They are **plans**, not 4 fills or wins.
- `data/public_dashboard_status.json`: published data is not proof of user notification or Pepperstone execution.
- The exact 4 November failure can be traced to a presentation-only 22-line `scripts/export_public_dashboard.py` exit-study export block added after the v17 hash was frozen. The current protocol incorrectly hashes exporter and validator source as if they were trade-decision dependencies. Any added behavioural change must have its own prospective protocol, never be backported into v17.

Baseline is anchored by immutable Git commit IDs and the archived v17 protocol, preserving existing ledgers. Synthetic lifecycle test events must not be added to these ledgers.

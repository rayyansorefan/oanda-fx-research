"""Publish a fail-closed operational status to the EXISTING FX feed only.

Never create a new market observation, trade, receipt or alert. A read-back
and optimistic-concurrency check is performed by the existing publisher.
"""
from __future__ import annotations
import copy
import os
import sys
from datetime import datetime, timezone
try:
    from scripts import export_public_dashboard as bridge
except ImportError:
    import export_public_dashboard as bridge


def unavailable_payload(current: dict, reason: str, observed_at: str) -> dict:
    """Retain true source age; invalidate all stale signals without rewriting history."""
    if not isinstance(current, dict) or not current.get("snapshot_at"):
        raise ValueError("Missing verifiable existing FX snapshot")
    payload = copy.deepcopy(current)
    payload["assessment_status"] = "ASSESSMENT_UNAVAILABLE"
    payload["health_checked_at"] = observed_at
    payload["unavailable_reason"] = reason
    payload["signals"] = []
    if isinstance(payload.get("hourly"), dict):
        payload["hourly"]["signals"] = []
    # Publication time is a health update only; market source time is unchanged.
    payload["published_at"] = observed_at
    return payload


def main() -> int:
    token = os.environ.get("FX_DASHBOARD_DEPLOY_TOKEN", "").strip()
    reason = os.environ.get("FX_HEALTH_FAILURE", "PIPELINE_UNAVAILABLE").strip()[:100]
    if not token:
        print("FX health fallback unavailable: missing existing dashboard deploy permission")
        return 1
    api = "https://api.github.com/repos/" + bridge.DESTINATION + "/contents/" + bridge.TARGET
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "User-Agent": "fx-research-dashboard-publisher"}
    try:
        _, current = bridge._existing_public_feed(api, headers)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if current.get("assessment_status") == "ASSESSMENT_UNAVAILABLE" and current.get("unavailable_reason") == reason:
            # Already unhealthy. Never write a new assessment time merely to look fresh.
            return 0
        candidate = unavailable_payload(current, reason, now)
        state = bridge.publish(candidate, token)
        bridge.status(state, "Operational failure; market snapshot timestamp unchanged", candidate["snapshot_at"])
        return 0
    except Exception as exc:
        # No token, private payload, or request headers are written to logs.
        print("FX health publication failed:", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

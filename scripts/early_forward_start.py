"""Independent same-day prospective side-cohort; preserves frozen 5 Nov protocols.

Reuses the exact registered v21 champion/challenger analysis and frozen simple-v2
strategy thresholds. Never relabels historical rows, creates trades or changes
the canonical delivery ledger. The 4 November cohort has independent files,
an immutable protocol digest and explicit limits on actual observations.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts import forward_validation as validation
    from scripts import simple_paper_shadow_v2 as simple
except ModuleNotFoundError:
    import forward_validation as validation
    import simple_paper_shadow_v2 as simple

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/validation_protocols/FX-EARLY-20261104-v1.json"
EARLY_SIMPLE = ROOT / "data/early_simple_paper_shadow.json"
EARLY_REPORT = ROOT / "data/early_forward_validation.json"
ID = "FX-EARLY-20261104-v1"


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def check_protocol(now):
    protocol = read(PROTOCOL, {})
    parent = read(ROOT / "validation_protocol.json", {})
    original_simple = read(simple.PROTOCOL, {})
    if (protocol.get("id") != ID or protocol.get("no_orders") is not True
            or protocol.get("parent_protocol_id") != "FX-OOS-20261104-v21"
            or parent.get("id") != "FX-OOS-20261104-v21"
            or parent.get("holdout_start_at") != "2026-11-05T07:00:00Z"
            or parent.get("rule_sha256") != protocol.get("rule_sha256")
            or original_simple.get("id") != "FX-SIMPLE-20261104-v2"
            or original_simple.get("holdout_start_at") != "2026-11-05T07:00:00Z"
            or original_simple.get("script_blob_sha1") != protocol.get("simple_shadow_script_blob_sha1")
            or protocol.get("simple_shadow_version") != "simple-shadow-early-20261104-v1"):
        raise ValueError("EARLY_COHORT_PARENT_PROTOCOL_MISMATCH")
    registered = simple.when(protocol.get("registered_at"))
    start = simple.when(protocol.get("holdout_start_at"))
    if (registered is None or start is None or registered >= start
            or start.date().isoformat() != "2026-11-04"):
        raise ValueError("EARLY_COHORT_NOT_PROSPECTIVELY_REGISTERED")
    # Verify original v2 script and every frozen dependency before reusing it.
    simple.validate_protocol(original_simple)
    if validation.digest_rules() != protocol["rule_sha256"]:
        raise ValueError("EARLY_COHORT_STRATEGY_RULES_CHANGED")
    return protocol, validation.protocol_digest(protocol)


def run(summary=None, news=None, *, now=None):
    now = (simple.when(now) if isinstance(now, str) else now) or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware observation required")
    protocol, digest = check_protocol(now)
    early = read(EARLY_SIMPLE)
    older = read(EARLY_REPORT)
    for prior in (early, older):
        if prior and prior.get("locked_protocol_sha256") != digest:
            raise ValueError("EARLY_COHORT_LOCK_CONFLICT")
    if summary is None:
        summary = read(ROOT / "data/summary.json", {})
    if news is None:
        news = read(ROOT / "data/news_latest.json", {})
    # Only an isolated runtime override; the registered simple-v2 file,
    # 5 November ledger, source hash and rule thresholds are not modified.
    original_start, original_version = simple.START, simple.VERSION
    try:
        simple.START = protocol["holdout_start_at"]
        simple.VERSION = protocol["simple_shadow_version"]
        updated = simple.update(summary, news, early, now=now)
    finally:
        simple.START, simple.VERSION = original_start, original_version
    updated["cohort_id"] = ID
    updated["locked_protocol_sha256"] = digest
    updated["source_rule_protocol_id"] = "FX-SIMPLE-20261104-v2"
    updated["independent_5_november_cohort_untouched"] = True

    # Comparator uses genuine existing future champion/challenger observations.
    # Its lock is separate and in-memory; NEVER rewrite the official delivery
    # ledger's immutable v21 hash or call the original v21 lock migration.
    ledger = read(ROOT / "data/signal_delivery_audit.json", {"events": {}})
    local_ledger = {**ledger, "locked_protocol_sha256": digest}
    study = read(ROOT / "data/strategy_comparison.json", {})
    report = validation.evaluate(
        protocol, study, local_ledger, validation.digest_rules(), now)
    report["cohort_id"] = ID
    report["locked_protocol_sha256"] = digest
    report["parent_protocol_id"] = "FX-OOS-20261104-v21"
    report["source_rule_protocol_unchanged"] = True
    report["canonical_delivery_ledger_unchanged"] = True
    report["paper_simple"] = {
        "status": updated["status"],
        "study_version": updated["study_version"],
        "metrics": updated["metrics"],
        "source_observations": len(updated.get("source_snapshots", [])),
    }
    report["no_real_broker_orders"] = True
    report["independent_5_november_cohort_untouched"] = True
    # Both outputs persist in the normal existing workflow data commit.
    write(EARLY_SIMPLE, updated)
    write(EARLY_REPORT, report)
    print("Early side-cohort", report["status"], "simple",
          updated["status"], "prospective plans", updated["metrics"]["plans"])
    return report, updated


if __name__ == "__main__":
    run()

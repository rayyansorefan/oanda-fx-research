"""Retain proven fills through outages and reject contradictory replay economics."""
from copy import deepcopy

TERMINAL = {"WIN_TP1", "LOSS_STOP", "TIME_EXIT"}
ENTRY_KEYS = ("entry", "entry_at", "triggered_at", "route")


def reconcile(record, candidate, checked_at):
    old = record.get("result") or {}
    if old.get("status") in TERMINAL and old.get("scored") is True:
        return old
    if not old.get("entry_at"):
        return candidate
    incompatible = (not candidate.get("entry_at") or any(
        key in old and old.get(key) != candidate.get(key) for key in ENTRY_KEYS))
    if incompatible:
        retained = deepcopy(old)
        retained["data_status"] = "REPLAY_UNVERIFIABLE"
        retained["last_failed_check_at"] = checked_at
        diagnostic = {"at": checked_at, "candidate": deepcopy(candidate)}
        history = record.setdefault("replay_conflicts", [])
        if not history or history[-1]["candidate"] != diagnostic["candidate"]:
            history.append(diagnostic)
        # Keep original entry evidence and open exposure; never invent an exit.
        return retained
    original = record.setdefault("original_entry_evidence", deepcopy(old))
    if old.get("source_evidence"):
        paths = record.setdefault("source_evidence_history", [])
        if old["source_evidence"] not in paths:
            paths.append(old["source_evidence"])
    candidate = deepcopy(candidate)
    candidate.setdefault("original_entry_evidence", deepcopy(original))
    return candidate

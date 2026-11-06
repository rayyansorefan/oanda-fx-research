"""Operational envelope for frozen collectors. No strategy or protocol changes.

Calls the original collection/scanning functions unchanged. Retains the last
valid source on failure and stores independent attempt clocks and run IDs.
The original bounded HTTP retry policy remains authoritative.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
try:
    from scripts import oanda_data as market, fast_entry_scan as fast
except ModuleNotFoundError:
    import oanda_data as market
    import fast_entry_scan as fast

ROOT = Path(__file__).resolve().parents[1]
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2)+"\n")

def run_collection():
    """Keep last verified source bytes; failed attempts have their own clock.

    Back up the source set before collection because collect writes several
    files. Restore the whole set on any caught failure, including partial
    filesystem publication. Never restore or modify prospective paper ledgers.
    """
    paths = (market.OUT, Path("data/summary.json"), Path("data/universe.json"), Path("data/pairs"))
    started = now()
    with tempfile.TemporaryDirectory(prefix="fx-source-") as directory:
        backups = []
        for i, path in enumerate(paths):
            backup = Path(directory) / str(i)
            if path.is_dir():
                shutil.copytree(path, backup)
            elif path.exists():
                shutil.copy2(path, backup)
            backups.append((path, backup))
        try:
            market.collect()
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            for path, backup in backups:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
                if backup.is_dir():
                    shutil.copytree(backup, path)
                elif backup.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, path)
            # Only allow known non-sensitive error identifiers into evidence.
            code = str(exc)
            if code == "Missing OANDA_API_TOKEN secret":
                code = "OANDA_TOKEN_MISSING"
            elif code.startswith("Set OANDA_ACCOUNT_ID"):
                code = "OANDA_ACCOUNT_CONFIGURATION_REQUIRED"
            elif not (code.startswith("OANDA_") or code in
                    ("INSUFFICIENT_COMPLETED_CANDLES", "MISSING_PAIRS_IN_PRICING")):
                code = type(exc).__name__
            record_collection_attempt(started, "DATA_UNAVAILABLE", code)
            print("Data unavailable:", code, file=sys.stderr)
            return 1
    record_collection_attempt(started, "COLLECTED", None)
    return 0


def record_collection_attempt(started, status, error):
    report = {"started_at": started, "checked_at": now(), "status": status,
              "error": error, "last_valid_source_preserved": status == "DATA_UNAVAILABLE",
              "run_id": os.environ.get("GITHUB_RUN_ID"),
              "event": os.environ.get("GITHUB_EVENT_NAME"),
              "oanda_environment": (os.environ.get("OANDA_ENVIRONMENT", "practice").strip().lower()
                                    if os.environ.get("OANDA_ENVIRONMENT", "practice").strip().lower()
                                    in ("practice", "live") else "INVALID")}
    write_json(Path("data/collection_attempt.json"), report)
    identity = os.environ.get("GITHUB_RUN_ID")
    if identity and identity.isdigit():
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        if attempt.isdigit():
            write_json(Path("data/collection_attempts") / (identity + "-" + attempt + ".json"), report)




def run_fast():
    try:
        result = fast.perform()
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        code = str(exc)
        if code == "Missing OANDA_API_TOKEN secret":
            code = "OANDA_TOKEN_MISSING"
        elif code.startswith("Set OANDA_ACCOUNT_ID"):
            code = "OANDA_ACCOUNT_CONFIGURATION_REQUIRED"
        elif not code.startswith("OANDA_"):
            code = type(exc).__name__
        result = {"checked_at": datetime.now(timezone.utc).isoformat(),
                  "status": "FAST_MARKET_DATA_UNAVAILABLE", "error": code,
                  "analysed": 0, "observations": [], "actionable_count": 0,
                  "user_delivery_verified": False}
        fast.write(fast.FAST_AUDIT, result)
    # Exit zero only after a genuine new observation. Preserve the previous
    # fast snapshot and history on failure; do not re-date either of them.
    result["run_id"] = os.environ.get("GITHUB_RUN_ID")
    result["event"] = os.environ.get("GITHUB_EVENT_NAME")
    if result.get("status") == "FAST_RESEARCH_CHECK_COMPLETE":
        snapshot = fast.read(fast.FAST_LATEST, {})
        result["pair_evidence"] = {
            pair: {"quote_at": item.get("quote", {}).get("time"),
                   "completed_candle_open_times": {
                       tf: frame.get("completed_at")
                       for tf, frame in item.get("frames", {}).items()}}
            for pair, item in snapshot.get("pairs", {}).items()}
    fast.write(fast.FAST_AUDIT, result)
    identity = os.environ.get("GITHUB_RUN_ID")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if identity and identity.isdigit() and attempt.isdigit():
        fast.write(ROOT / "data/fast_attempts" / (identity + "-" + attempt + ".json"), result)
    print("Fast scan outcome:", result["status"], result.get("error", ""))
    return 0 if result.get("status") == "FAST_RESEARCH_CHECK_COMPLETE" else 1



if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("full", "fast"):
        raise SystemExit("Use full or fast")
    raise SystemExit(run_collection() if sys.argv[1] == "full" else run_fast())

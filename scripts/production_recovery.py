"""Operational envelope for frozen collectors. No strategy or protocol changes.

Calls the original collection/scanning functions unchanged. Retains the last
valid source on failure and stores independent attempt clocks and run IDs.
The original bounded HTTP retry policy remains authoritative.
"""
import json
import os
import shutil
import time
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
try:
    from scripts import oanda_data as market, fast_entry_scan as fast
except ModuleNotFoundError:
    import oanda_data as market
    import fast_entry_scan as fast

try:
    from scripts import oanda_auth_diagnostics as auth
except ModuleNotFoundError:
    import oanda_auth_diagnostics as auth

ROOT = Path(__file__).resolve().parents[1]
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2)+"\n")

# Operational-only second try after an observed intermittent practice-host failure.
# The frozen collector, OANDA request's own retry policy and signal rules remain
# unchanged. Credential/permission failures are NOT silently reclassified.
BOUNDED_SOURCE_RECOVERY_ERRORS = frozenset({
    "OANDA_HTTP_401_ACCOUNTS", "OANDA_HTTP_504_ACCOUNTS",
    "OANDA_HTTP_401_INSTRUMENTS", "OANDA_HTTP_504_INSTRUMENTS",
})


def run_collection():
    """Try a failed full-source collection once more without destroying evidence.

    An observed transient-looking 401/504 on account discovery or instruments
    may be followed by a successful request under an unchanged valid token.
    Never switch hosts/tokens, suppress persistent auth failure, reuse old
    quotes as fresh or repeat a historical GitHub workflow.
    """
    paths = (market.OUT, Path("data/summary.json"), Path("data/universe.json"), Path("data/pairs"))
    started = now()
    first_error = None
    with tempfile.TemporaryDirectory(prefix="fx-source-") as directory:
        backups = []
        for i, path in enumerate(paths):
            backup = Path(directory) / str(i)
            if path.is_dir():
                shutil.copytree(path, backup)
            elif path.exists():
                shutil.copy2(path, backup)
            backups.append((path, backup))

        def restore_last_valid():
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

        for attempt in (1, 2):
            observer = auth.Observer()
            try:
                with observer.capture(market):
                    market.collect()
            except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                code = observer.error_for(exc)
                restore_last_valid()
                if attempt == 1 and code in BOUNDED_SOURCE_RECOVERY_ERRORS:
                    first_error = code
                    print("OANDA full-source request failed at", code,
                          "- one bounded independent collection recovery", file=sys.stderr)
                    time.sleep(2)
                    continue
                final_error = observer.probe(market, code)
                record_collection_attempt(started, "DATA_UNAVAILABLE", final_error,
                                          collection_tries=attempt, first_error=first_error,
                                          diagnostics=observer.summary())
                print("Data unavailable:", final_error, file=sys.stderr)
                return 1
            else:
                record_collection_attempt(started, "COLLECTED", None,
                                          collection_tries=attempt, first_error=first_error,
                                          diagnostics=observer.summary())
                if first_error:
                    print("OANDA source recovered after first failure:", first_error)
                return 0
    raise AssertionError("unreachable bounded OANDA collection recovery")


def record_collection_attempt(started, status, error, *, collection_tries=1, first_error=None, diagnostics=None):
    report = {"started_at": started, "checked_at": now(), "status": status,
              "error": error, "last_valid_source_preserved": status == "DATA_UNAVAILABLE",
              "run_id": os.environ.get("GITHUB_RUN_ID"),
              "event": os.environ.get("GITHUB_EVENT_NAME"),
              "collector_endpoint": auth.safe_host(market),
              "collection_tries": collection_tries,
              "first_attempt_error": first_error,
              "recovered_on_second_try": status == "COLLECTED" and collection_tries == 2,
              "request_diagnostics": diagnostics or {}}
    write_json(Path("data/collection_attempt.json"), report)
    identity = os.environ.get("GITHUB_RUN_ID")
    if identity and identity.isdigit():
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        if attempt.isdigit():
            write_json(Path("data/collection_attempts") / (identity + "-" + attempt + ".json"), report)




def run_fast():
    observer = auth.Observer()
    try:
        with observer.capture(fast.market):
            result = fast.perform()
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        code = observer.probe(fast.market, observer.error_for(exc))
        result = {"checked_at": datetime.now(timezone.utc).isoformat(),
                  "status": "FAST_MARKET_DATA_UNAVAILABLE", "error": code,
                  "analysed": 0, "observations": [], "actionable_count": 0,
                  "user_delivery_verified": False}
        fast.write(fast.FAST_AUDIT, result)
    # The frozen fast scanner sometimes reports a generic no-quotes error
    # after catching per-batch 401. Preserve the actual observed failure stage.
    if result.get("status") != "FAST_RESEARCH_CHECK_COMPLETE" and observer.first_auth():
        result["error"] = observer.probe(fast.market, observer.first_auth())
    result["collector_endpoint"] = auth.safe_host(fast.market)
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

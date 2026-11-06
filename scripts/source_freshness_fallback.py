"""Read-only full-source preflight for independent scheduled fast backup.

Run the existing frozen OANDA collector only if its hourly source is too old.
No new FX model, orders, guessed prices or repeated API loops. Failures restore
the tracked source evidence so a failed retry cannot replace the last good data.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"data/summary.json"
UNIVERSE=ROOT/"data/universe.json"
OUTPUT=ROOT/"data/fast_source_preflight.json"
MAX_AGE=timedelta(minutes=45)
RESTORE=("data/summary.json","data/latest.json","data/universe.json","data/pairs")


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError,ValueError,TypeError):
        return {}


def when(value):
    try:
        t=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (ValueError,OverflowError,TypeError):
        return None


def valid(summary,universe,now):
    t=when(summary.get("collected_at"))
    return bool(summary.get("status") in ("OK","PARTIAL")
                and isinstance(summary.get("pairs"),dict) and summary["pairs"]
                and t and timedelta(0) <= now-t <= MAX_AGE
                and universe.get("collected_at")==summary["collected_at"])


def collect(*,now=None,invoke=None,restore=None):
    now=now or datetime.now(timezone.utc)
    if valid(read(SOURCE),read(UNIVERSE),now):
        return {"status":"EXISTING_FULL_SOURCE_FRESH",
                "refreshed":False,"checked_at":now.isoformat(),
                "source_at":read(SOURCE)["collected_at"]}
    old=read(SOURCE).get("collected_at")
    run=invoke or (lambda:subprocess.run(
        [sys.executable,"scripts/production_recovery.py","full"],cwd=ROOT,check=False).returncode)
    result=run()
    observed=datetime.now(timezone.utc)
    summary=read(SOURCE)
    universe=read(UNIVERSE)
    if result != 0 or not valid(summary,universe,observed):
        # Only uncommitted working-tree source outputs are restored. Never
        # touch any paper ledgers, prior Git commits or registered protocols.
        if restore is None:
            proc=subprocess.run(["git","restore","--worktree","--",*RESTORE],
                                cwd=ROOT,check=False)
            if proc.returncode:
                raise RuntimeError("FAILED_FULL_SOURCE_AND_RESTORE_FAILED")
        else:
            restore()
        raise RuntimeError("NO_FRESH_OANDA_SOURCE_RETAINED_PREVIOUS_EVIDENCE")
    if old and when(old) and when(summary["collected_at"]) <= when(old):
        if restore is None:
            subprocess.run(["git","restore","--worktree","--",*RESTORE],cwd=ROOT,check=True)
        else:
            restore()
        raise RuntimeError("FULL_SOURCE_TIMESTAMP_NOT_ADVANCED")
    return {"status":"FRESH_FULL_SOURCE_RECOVERED",
            "refreshed":True,"checked_at":observed.isoformat(),
            "source_at":summary["collected_at"]}


def main():
    result=collect()
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    output=os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output,"a") as stream:
            stream.write("refreshed="+str(result["refreshed"]).lower()+"\n")
    print("Fast source preflight",result["status"],result["source_at"])


if __name__=="__main__":
    main()

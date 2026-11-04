"""Private, read-only audit of persisted economics, forward samples and run clocks.

GitHub run creation is not cron's intended dispatch time or user delivery.
No broker connection, inferred fills, evidence resets or automatic promotion.
"""
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TARGET = "AUTO-20261029T0321-EUR_AUD-S-B"


def read(name, default=None):
    p=ROOT/name
    return json.loads(p.read_text()) if p.exists() else (default if default is not None else {})


def at(s):
    return datetime.fromisoformat(s.replace("Z","+00:00")) if s else None


def economics(p):
    fields=("instrument","direction","created_at","expires_at","planned_entry",
            "entry_low","entry_high","stop","tp1","tp2","alert_a","alert_b","alert_c",
            "max_oanda_spread_pips","event_blackouts_utc")
    return {k:p.get(k, [] if k=="event_blackouts_utc" else None) for k in fields}


def distribution(values):
    values=sorted(v for v in values if v is not None)
    return ({"count":len(values),"median_seconds":values[len(values)//2],
             "p95_seconds":values[min(len(values)-1,int(.95*len(values)))],
             "max_seconds":values[-1]} if values else {"count":0})


def github(path):
    token=os.environ.get("GITHUB_TOKEN")
    headers={"Accept":"application/vnd.github+json","User-Agent":"fx-independent-audit"}
    if token: headers["Authorization"]="Bearer "+token
    with urlopen(Request("https://api.github.com/repos/"+path,headers=headers),timeout=20) as r:
        return json.load(r)


def build():
    now=datetime.now(timezone.utc)
    protocol=read("validation_protocol.json")
    lifecycle=read("data/lifecycle_ledger.json")
    canonical=next((r for r in lifecycle.get("plans",{}).values()
                    if r.get("id")==TARGET and r.get("source_kind")=="champion_manual_plan"),{})
    original=economics(canonical.get("original",{}))
    matches=[]
    for row in read("data/paper_trade_ledger.json").get("candidates",{}).values():
        p=row.get("plan",{})
        if p.get("id")!=TARGET:continue
        actual=economics(p)
        mismatch=[]
        for k in original:
            a,b=original[k],actual[k]
            if k in ("created_at","expires_at"):
                a,b=at(a),at(b)
            if a!=b:mismatch.append(k)
        result=row.get("result",{})
        matches.append({"economic_terms":actual,"mismatched_fields":mismatch,
                        "result":result,"first_recorded_at":row.get("first_recorded_at")})
    study=read("data/strategy_comparison.json")
    arms={}
    start=at(protocol["holdout_start_at"])
    for arm in ("champion","challenger_v1"):
        rows=[r for r in study.get("records",[]) if r.get("policy")==arm and r.get("plan")
              and at(r["plan"]["created_at"])>=start and at(r.get("decision_at"))
              and at(r["decision_at"])>=start]
        results=[r.get("result",{}) for r in rows]
        scored=[r for r in results if r.get("scored") is True]
        equity=peak=drawdown=0
        for r in sorted(scored,key=lambda r:r.get("at", "")):
            equity+=r["net_r"];peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
        arms[arm]={"plans":len(rows),"triggers":sum(bool(r.get("triggered_at")) for r in results),
                   "fills":sum(bool(r.get("entry_at")) for r in results),
                   "outcomes":dict(Counter(r.get("status","PENDING") for r in results)),
                   "exits":len(scored),"net_r":equity if scored else None,
                   "drawdown_r":drawdown if scored else None}
    report={"checked_at":now.isoformat(),"protocol_id":protocol["id"],
            "source_at":read("data/summary.json").get("collected_at"),
            "eur_aud":{"original":original,"canonical_state":canonical.get("current_state"),
                       "canonical_r":canonical.get("net_r"),"underlying_paper_records":matches,
                       "active_issues":lifecycle.get("active_issues"),
                       "historical_issues":lifecycle.get("historical_only_issues"),
                       "broker_execution_verified":False},
            "forward_results":arms,
            "simple_v1":read("data/simple_paper_shadow.json").get("metrics"),
            "simple_v2":read("data/simple_paper_shadow_v2.json").get("metrics"),
            "exit_study":read("data/exit_management_research.json").get("metrics"),
            "notification_delivery_verified":False,"performance_validated":False}
    try:
        runs=github("rayyansorefan/oanda-fx-research/actions/runs?per_page=100")["workflow_runs"]
        rows=[{k:r.get(k) for k in ("id","name","event","status","conclusion",
                                    "created_at","run_started_at","updated_at","head_sha","html_url")}
              for r in runs]
        scheduled=[r for r in rows if r["event"]=="schedule"]
        report["workflow_runs"]=rows
        report["scheduler_measurement"]={
            "scope":"Latest 100 run objects; run creation to runner start only. Intended cron dispatch unavailable.",
            "queue_delay":distribution([(at(r["run_started_at"])-at(r["created_at"])).total_seconds()
                                           for r in scheduled if r.get("run_started_at")]),
            "scheduled_run_count":len(scheduled),
            "five_minute_sla_proven":False,"missed_cron_slots_verified":None}
        report["recent_job_steps"]={}
        for r in [x for x in rows if x["name"] in
                  ("OANDA FX read-only research data","FX fast shortlisted entry research (read-only)")][:4]:
            jobs=github("rayyansorefan/oanda-fx-research/actions/runs/"+str(r["id"])+"/jobs").get("jobs",[])
            report["recent_job_steps"][str(r["id"])]=[
                {"id":j["id"],"conclusion":j.get("conclusion"),"steps":j.get("steps",[])} for j in jobs]
        report["pages_runs"]=[{k:r.get(k) for k in ("id","name","event","status","conclusion",
                                  "created_at","run_started_at","updated_at","head_sha","html_url")}
                               for r in github("rayyansorefan/standalone-wallet-paper-agent/actions/runs?per_page=10").get("workflow_runs",[])]
    except Exception as exc:
        report["workflow_evidence_error"]=type(exc).__name__
    return report


if __name__=="__main__":
    report=build()
    (ROOT/"data/independent_implementation_audit.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print("Private independent implementation evidence recorded; performance unvalidated.")

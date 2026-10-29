"""Locked prospective validation; measures evidence, never promotes or sends alerts.

Creation, trigger detection, repository publication, observed public availability
and verified user delivery are deliberately separate. No receipt is invented.
"""
from __future__ import annotations
import hashlib
import json
import random
import math
import sys
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.request import Request,urlopen
from zoneinfo import ZoneInfo
try:
    from scripts.signal_admission import moment
except ModuleNotFoundError:
    from signal_admission import moment
ROOT=Path(__file__).resolve().parents[1]
LONDON=ZoneInfo('Europe/London')
PUBLIC_FEED='https://rayyansorefan.github.io/standalone-wallet-paper-agent/fx-feed.json'
RULE_FILES=('scripts/signal_admission.py','scripts/adaptive_research.py','scripts/news_watch.py',
            'scripts/fx_universe.py','scripts/paper_trade_audit.py','scripts/signal_watch.py',
            'scripts/strategy_comparison.py','scripts/forward_validation.py',
            'scripts/oanda_data.py','scripts/advanced_technicals.py','scripts/entry_readiness.py','scripts/export_public_dashboard.py',
            'scripts/fast_entry_scan.py','.github/workflows/oanda-fast-entry.yml')

def digest_rules(root=ROOT):
    h=hashlib.sha256()
    for name in RULE_FILES:
        h.update(name.encode()+b'\0'+(root/name).read_bytes()+b'\0')
    return h.hexdigest()

def coverage(summary,admission):
    pairs=summary.get('pairs') or {};ranks=summary.get('ranked_review_candidates') or []
    deep={p for p,v in pairs.items() if all((v.get('frames',{}).get(tf,{}) or {}).get('indicators') for tf in ('M15','H1','H4','D'))}
    strict={r['instrument'] for r in ranks if r.get('deep_entry_scout_eligible')}
    paper={r['instrument'] for r in ranks if r.get('paper_deep_scout_eligible')}
    rows=admission.get('candidate_routes') or []
    reasons=defaultdict(set)
    for r in rows:
        if r.get('reason'):reasons[r['reason']].add(r['pair'])
    return {'source_at':summary.get('collected_at'),'quoted_pairs':len(pairs),'deep_pairs':len(deep),
        'strict_prequalified':sorted(strict),'strict_prequalified_missing_deep':sorted(strict-deep),
        'paper_prequalified':sorted(paper),'paper_prequalified_missing_deep':sorted(paper-deep),
        'paper_prequalification_measured':any('paper_deep_scout_eligible' in r for r in ranks),
        'route_evaluations':len(rows),'distinct_evaluated_pairs':len({r['pair'] for r in rows}),
        'route_rejections':dict(Counter(r['reason'] for r in rows if r.get('reason'))),
        'distinct_pairs_by_rejection':{k:len(v) for k,v in sorted(reasons.items())},
        'count_note':'Rejection categories overlap; route counts and per-reason pair counts must not be summed into independent trades.'}

def record_events(book,signals,watcher,publication,now):
    events=book.setdefault('events',{})
    source=watcher.get('oanda_collected_at')
    for plan in signals.get('signals') or []:
        key='PLAN:'+plan['id']
        events.setdefault(key,{'id':key,'plan_id':plan['id'],'kind':'CONDITIONAL_PLAN',
            'source_at':plan.get('oanda_snapshot_at',source),'plan_created_at':plan.get('created_at'),
            'trigger_closed_at':None,'detected_at':None,'verified_user_delivery_at':None,
            'delivery_status':'NOT_MEASURED'})
    for row in watcher.get('signals') or []:
        if row.get('status')!='PRICE_REASSESSMENT_ALERT_READY':continue
        opened=moment(row.get('trigger_bar_open_time'))
        if not opened:continue
        key='TRIGGER:'+row['id']+':'+str(row.get('route'))+':'+opened.isoformat()
        events.setdefault(key,{'id':key,'plan_id':row['id'],'kind':'TRIGGER_DETECTED',
            'source_at':source,'plan_created_at':row.get('created_at'),
            'trigger_closed_at':(opened+timedelta(minutes=15)).isoformat(),
            'detected_at':watcher.get('collected_at'),'verified_user_delivery_at':None,
            'delivery_status':'NOT_MEASURED','route':row.get('route'),
            'trigger_bar_open_time':row.get('trigger_bar_open_time')})
    if publication.get('status')=='PUBLISHED':
        for event in events.values():
            if event['source_at']==publication.get('snapshot_at'):
                event.setdefault('repository_readback_at',publication.get('checked_at'))
    book['updated_at']=now.isoformat()
    return book

def merge_fast_events(book, fast):
    """Import detection clocks, never manufacture a user-delivery receipt."""
    events = book.setdefault('events', {})
    for key, item in (fast.get('events') or {}).items():
        if not isinstance(item, dict) or not item.get('first_detected_at'):
            continue
        name = 'FAST_TRIGGER:' + key
        events.setdefault(name, {
            'id': name, 'plan_id': item.get('plan_id'),
            'kind': 'TRIGGER_DETECTED',
            'source_at': item.get('source_snapshot_at'),
            'plan_created_at': item.get('plan_created_at'),
            'trigger_closed_at': item.get('trigger_closed_at'),
            'detected_at': item.get('first_detected_at'),
            'verified_user_delivery_at': None,
            'delivery_status': 'NOT_MEASURED',
            'verification_kind': None,
            'verification_source_reference': None,
            'route': item.get('route'),
            'trigger_bar_open_time': item.get('trigger_bar_open_time'),
        })
    return book


def observe_public(book,feed,observed_at):
    """An observed serving time is an upper bound, not a push delivery receipt."""
    for event in book.get('events',{}).values():
        expected_source = (feed.get('hourly') or {}).get('source_at')
        if event.get('source_at') not in (feed.get('snapshot_at'), expected_source):continue
        if event['kind']=='CONDITIONAL_PLAN':
            present=any(s.get('id')==event['plan_id'] for s in feed.get('signals') or [])
        else:
            present=any(s.get('id')==event['plan_id'] and s.get('route')==event.get('route')
                        and s.get('trigger_bar_open_time')==event.get('trigger_bar_open_time')
                        and s.get('status')=='PRICE_REASSESSMENT_ALERT_READY'
                        for s in (feed.get('hourly') or {}).get('signals') or [])
        if present:event.setdefault('first_observed_public_at',observed_at.isoformat())
    return book

def number_cost(value):
    try:
        x=float(value)
        return x if math.isfinite(x) and x>=0 else None
    except (ValueError,TypeError):return None

def stress_result(plan,result,slippage_pips):
    if result.get('scored') is not True:return None
    try:
        entry=float(result['entry']);stop=float(plan['stop']);exit_price=float(result['exit'])
        pip=number_cost(plan.get('pip_size'))
        if not pip:return {'status':'SOURCE_PIP_SIZE_UNKNOWN','net_r':None}
        long=plan['direction']=='LONG';gap_adjusted=False
        exit_bar=((result.get('evidence') or {}).get('exit') or {}).get('bid' if long else 'ask')
        if result['status']=='LOSS_STOP':
            if not exit_bar:return {'status':'EXIT_GAP_EVIDENCE_MISSING','net_r':None}
            opening=float(exit_bar['o']);adverse=min(exit_price,opening) if long else max(exit_price,opening)
            gap_adjusted=adverse!=exit_price;exit_price=adverse
        risk=abs(entry-stop)
        if risk<=0 or not all(math.isfinite(x) for x in (entry,stop,exit_price,risk,slippage_pips)) or slippage_pips<0:return None
        value=((exit_price-entry)*(1 if long else -1)-2*slippage_pips*pip)/risk
        broker_cost=number_cost(result.get('additional_broker_cost_r'))
        if result.get('broker_costs_verified') is True and broker_cost is not None:value-=broker_cost
        opened=moment(result.get('entry_at'));closed=moment(result.get('at'))
        if not opened or not closed or closed<opened:return {'status':'TIMING_EVIDENCE_MISSING','net_r':None}
        # Missing broker financing is explicitly unresolved if a NY 17:00 rollover was crossed.
        ny=ZoneInfo('America/New_York');day=opened.astimezone(ny).date();end=closed.astimezone(ny).date()
        rollover=False
        while day<=end:
            cutoff=datetime.combine(day,datetime.min.time(),tzinfo=ny)+timedelta(hours=17)
            if opened<cutoff<=closed:rollover=True
            day+=timedelta(days=1)
        return {'status':'FINANCING_UNKNOWN' if rollover and result.get('financing_cost_verified') is not True else 'OANDA_COST_SCENARIO',
                'net_r':round(value,6),'stop_gap_adjusted':gap_adjusted,'slippage_pips_per_side':slippage_pips,
                'broker_costs_verified':False,'financing_crossed':rollover}
    except (KeyError,TypeError,ValueError,ZeroDivisionError):return None

def delivered_before_entry(row,ledger,now):
    result=row.get('result') or {};entry=moment(result.get('entry_at'))
    if not entry:return False
    for event in (ledger.get('events') or {}).values():
        if event.get('plan_id')!=row['plan']['id'] or event.get('kind')!='TRIGGER_DETECTED':continue
        received=moment(event.get('verified_user_delivery_at'));detected=moment(event.get('detected_at'))
        if (event.get('verification_kind') in ('PROVIDER_DELIVERED_RECEIPT','USER_ACKNOWLEDGEMENT')
                and event.get('verification_source_reference') and received and detected
                and detected<=received<=now and received+timedelta(minutes=1)<=entry):return True
    return False

def bootstrap_days(differences,resamples=10000):
    if len(differences)<2:return None
    rng=random.Random(20261028);n=len(differences)
    means=sorted(sum(rng.choice(differences) for _ in range(n))/n for _ in range(resamples))
    return {'paired_days':n,'mean_improvement_r':sum(differences)/n,
            'lower_95pct_r':means[int(.025*resamples)],'upper_95pct_r':means[int(.975*resamples)-1],
            'method':'Pre-registered day-cluster bootstrap; abstention contributes zero; overlapping pair exposures are clustered by day.'}

def protocol_digest(protocol):
    return hashlib.sha256(json.dumps(protocol,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def evaluate(protocol,study,ledger,current_hash,now):
    start=moment(protocol['holdout_start_at'])
    unchanged=(current_hash==protocol['rule_sha256'] and
               ledger.get('locked_protocol_sha256',protocol_digest(protocol))==protocol_digest(protocol))
    eligible=[];excluded=Counter();independent={}
    for row in study.get('records') or []:
        plan=row.get('plan')
        if not plan:continue
        decision=moment(row.get('decision_at'))
        if not decision or decision<start or not moment(plan.get('created_at')) or moment(plan['created_at'])<start:
            excluded['DEVELOPMENT_OR_LEGACY_TIMING']+=1;continue
        if row.get('observation_scope') != 'PAIR_SPECIFIC' and protocol.get('require_pair_specific_observations'):
            excluded['UNSCOPED_OBSERVATION_TIMING']+=1;continue
        if not row.get('common_deep_history_available'):
            excluded['INCOMPLETE_COMMON_UNIVERSE']+=1;continue
        if not unchanged:
            excluded['PROTOCOL_CHANGED']+=1;continue
        eligible.append(row)
        result=row.get('result') or {}
        if result.get('scored') is True and result.get('entry_at'):
            day=moment(result['entry_at']).astimezone(LONDON).date().isoformat()
            key=(row['policy'],plan['instrument'],day)
            old=independent.get(key)
            if old is None or result['entry_at']<old['result']['entry_at']:independent[key]=row
    arms={}
    for arm in ('champion','challenger_v1'):
        rows=[r for r in eligible if r['policy']==arm]
        scored=[r for k,r in independent.items() if k[0]==arm]
        scenarios={str(s):[stress_result(r['plan'],r['result'],s) for r in scored]
                   for s in protocol['slippage_pips_per_side']}
        arms[arm]={'prospective_plans':len(rows),'independent_scored_pair_days':len(scored),
            'distinct_scored_days':len({k[2] for k in independent if k[0]==arm}),
            'outcomes':dict(Counter((r.get('result') or {}).get('status','NOT_REPLAYED') for r in rows)),
            'cost_scenarios':scenarios,'verified_user_deliveries':sum(delivered_before_entry(r,ledger,now) for r in scored)}
    # Compare day-clustered outcomes only after the pre-registered sample floor.
    paired=None
    enough=all(a['independent_scored_pair_days']>=protocol['minimum_independent_scored_pair_days_per_arm']
               and a['distinct_scored_days']>=protocol['minimum_scored_days_per_arm'] for a in arms.values())
    stress_complete = bool(independent) and all(
        (stress_result(r['plan'], r['result'], max(protocol['slippage_pips_per_side'])) or {}).get('net_r') is not None
        for r in independent.values())
    if enough and stress_complete:
        daily=defaultdict(lambda:defaultdict(float))
        for (arm,pair,day),row in independent.items():
            stress=stress_result(row['plan'],row['result'],max(protocol['slippage_pips_per_side']))
            if stress and stress.get('net_r') is not None:daily[day][arm]+=stress['net_r']
        paired=bootstrap_days([v['challenger_v1']-v['champion'] for k,v in sorted(daily.items())],protocol['bootstrap_resamples'])
    delivery_ready=bool(independent) and all(delivered_before_entry(r,ledger,now) for r in independent.values())
    costs_ready=bool(independent) and all(
        r['result'].get('broker_costs_verified') is True and number_cost(r['result'].get('additional_broker_cost_r')) is not None
        and (stress_result(r['plan'],r['result'],max(protocol['slippage_pips_per_side'])) or {}).get('status')=='OANDA_COST_SCENARIO'
        for r in independent.values())
    readiness={
        'holdout_started':now>=start,'rules_unchanged':unchanged,
        'minimum_independent_samples':all(a['independent_scored_pair_days']>=protocol['minimum_independent_scored_pair_days_per_arm'] for a in arms.values()),
        'minimum_scored_days':all(a['distinct_scored_days']>=protocol['minimum_scored_days_per_arm'] for a in arms.values()),
        'actual_delivery_evidence':delivery_ready,'broker_cost_evidence':costs_ready,
        'complete_cost_scenarios':stress_complete,
        'statistical_improvement_established':bool(paired and paired['lower_95pct_r']>0)}
    if not unchanged:
        status = 'PROTOCOL_CHANGED'
    elif now < start:
        status = 'HOLDOUT_NOT_STARTED'
    elif not enough:
        status = 'COLLECTING_HOLDOUT'
    elif not delivery_ready or not costs_ready:
        status = 'REQUIRED_DELIVERY_OR_BROKER_COST_EVIDENCE_MISSING'
    elif not paired:
        status = 'COMPARISON_UNAVAILABLE'
    elif paired['lower_95pct_r'] <= 0:
        status = 'NO_DEMONSTRATED_CHALLENGER_IMPROVEMENT'
    else:
        status = 'PASS_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW'
    return {'updated_at':now.isoformat(),'status':status,
        'pass_fail_decision':('REVIEW_ELIGIBLE' if all(readiness.values()) else
                              'NO_DEMONSTRATED_IMPROVEMENT' if enough and paired and
                              paired['lower_95pct_r'] <= 0 else 'NOT_YET_VALIDATED'),
        'independent_review_completed':False,
        'protocol_id':protocol['id'],'holdout_start_at':protocol['holdout_start_at'],
        'readiness':readiness,'arms':arms,'excluded':dict(excluded),
        'paired_day_comparison':paired,'evidence_ready_for_independent_review':all(readiness.values()),
        'delivery_events_recorded':len(ledger.get('events') or {}),
        'performance_validated':False,'automatic_promotion_allowed':False,
        'interpretation':'No rule relaxation or profitability claim. Historical replay, public availability and actual user delivery are different evidence. Missing delivery/broker costs and unestablished statistical improvement block validation.'}

def read(name,default):
    path=ROOT/name
    return json.loads(path.read_text()) if path.exists() else default

def lock_protocol(book, protocol, now):
    """Explicit version migration; retain old lock and every event/receipt."""
    old_lock = book.get('locked_protocol_sha256')
    if (old_lock and old_lock != protocol_digest(protocol)
            and old_lock == protocol.get('supersedes_protocol_sha256')):
        book.setdefault('prior_protocol_locks', []).append({
            'sha256': old_lock, 'superseded_at': now.isoformat(),
            'replacement_protocol_id': protocol['id']})
        book['locked_protocol_sha256'] = protocol_digest(protocol)
    book.setdefault('locked_protocol_sha256',protocol_digest(protocol))
    return book

def main():
    now=datetime.now(timezone.utc);protocol=read('validation_protocol.json',{})
    book=read('data/signal_delivery_audit.json',{'schema':1,'events':{}})
    lock_protocol(book, protocol, now)
    # Check the already deployed snapshot before recording a newly collected one.
    try:
        req=Request(PUBLIC_FEED,headers={'User-Agent':'FX-forward-validation-read-only'})
        with urlopen(req,timeout=10) as response:public=json.load(response)
        book=observe_public(book,public,now)
        book['public_check_status']='OBSERVED'
    except Exception as exc:book['public_check_status']='UNAVAILABLE:'+type(exc).__name__
    book=record_events(book,read('signals.json',{}),read('data/hourly_assessment.json',{}),
                       read('data/public_dashboard_status.json',{}),now)
    book=merge_fast_events(book,read('data/fast_entry_events.json',{}))
    (ROOT/'data/signal_delivery_audit.json').write_text(json.dumps(book,indent=2,sort_keys=True)+'\n')
    report=evaluate(protocol,read('data/strategy_comparison.json',{}),book,digest_rules(),now)
    report['coverage']=coverage(read('data/summary.json',{}),read('data/signal_admission_audit.json',{}))
    (ROOT/'data/forward_validation.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print('Forward validation',report['status'],'validated',report['performance_validated'])

if __name__=='__main__':main()

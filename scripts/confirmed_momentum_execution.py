"""Paired prospective execution/exit experiment, never a broker order or approval.

One completed liquid-momentum setup supplies all arms. Existing late/manual
confirmation is the control; precommitted next-M15-open simulation tests whether
an extra confirmation misses an already completed impulse. Entries and exits
use bid/ask, adverse slippage and original frozen economic levels. Old cohorts
are never imported as new forward evidence.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
import sys
from zoneinfo import ZoneInfo
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    from scripts import liquid_momentum_shadow_v1 as parent
    from scripts import simple_paper_shadow_v2 as control
    from scripts import paper_trade_audit as prices
except ModuleNotFoundError:
    import liquid_momentum_shadow_v1 as parent
    import simple_paper_shadow_v2 as control
    import paper_trade_audit as prices
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/confirmed_momentum_execution.json'
VERSION='confirmed-momentum-execution-v1'
ARMS=('EXISTING_EXTRA_CONFIRMATION','CONFIRMED_NEXT_OPEN','CONFIRMED_NEXT_OPEN_PROTECTED')
UTC=timezone.utc
SLIP_PIPS=.25
MAX_REPLAYS=4


def digest():
    h=hashlib.sha256(Path(__file__).read_bytes())
    h.update(parent.digest().encode())
    return h.hexdigest()


def entry_check(plan,bar,slip_pips=SLIP_PIPS):
    """Validate actual fill economics; never move the stop to manufacture a fit."""
    side=1 if plan['direction']=='LONG' else -1
    pip=float(plan['pip_size']); atr=float(plan['atr_at_decision'])
    bid,ask=bar['bid']['o'],bar['ask']['o']
    fill=(ask if side>0 else bid)+side*slip_pips*pip
    stop,target=float(plan['stop']),float(plan['tp1'])
    if not all(math.isfinite(v) for v in (pip,atr,bid,ask,fill,stop,target)) or pip<=0 or atr<=0 or not 0<bid<ask:
        return None,'INVALID_PRICE_OR_ATR'
    if (bid<=stop if side>0 else ask>=stop):return None,'STOP_ALREADY_INSIDE_EXIT_SIDE_QUOTE'
    risk=side*(fill-stop); reward=side*(target-fill); spread=ask-bid
    if risk<=0 or reward<=0:return None,'FROZEN_LEVELS_INVALID_AT_FILL'
    if spread/pip>plan['max_spread_pips']:return None,'SPREAD_CAP'
    if abs(fill-plan['indicative_entry'])>.35*atr:return None,'PRICE_CHASING'
    # Original risk floor1.1ATR less maximum chase0.35ATR =0.75ATR.
    if risk<.75*atr:return None,'STOP_TOO_CLOSE_RELATIVE_TO_VOLATILITY'
    # Remaining reward includes an additional0.5pip round-trip stress.
    cost=spread+2*slip_pips*pip
    if reward/risk<1.5 or (reward-cost)/(risk+cost)<1.35:return None,'AFTER_COST_REWARD_INADEQUATE'
    return {'entry':fill,'risk_initial':risk,'spread_at_fill':spread},None


def expected_weekend_gap(previous,following):
    if following-previous>timedelta(days=3):return False
    t=previous+timedelta(minutes=15)
    while t<following:
        ny=t.astimezone(ZoneInfo('America/New_York'))
        if not (ny.weekday()==5 or ny.weekday()==4 and ny.hour>=17 or ny.weekday()==6 and ny.hour<17):return False
        t+=timedelta(minutes=15)
    return True


def replay(plan,bars,now,protective=False):
    created=control.when(plan['created_at']); expiry=control.when(plan['expires_at'])
    available=created.replace(minute=created.minute//15*15,second=0,microsecond=0)+timedelta(minutes=15)
    complete=[b for b in bars if b['t']>=available and b['t']+timedelta(minutes=15)<=now]
    if not complete:return {'status':'PENDING_ENTRY' if now<=expiry else 'DATA_GAP','scored':False}
    if complete[0]['t']!=available:return {'status':'DATA_GAP','scored':False,'reason':'MISSING_PLANNED_ENTRY_BAR'}
    if prices.blackout(plan,available):return {'status':'ENTRY_REJECTED','scored':False,'reason':'FROZEN_EVENT_BLACKOUT'}
    if available>=expiry:return {'status':'EXPIRED_UNFILLED','scored':False}
    fill,error=entry_check(plan,complete[0])
    if error:return {'status':'ENTRY_REJECTED','scored':False,'reason':error}
    side=1 if plan['direction']=='LONG' else -1
    entry,risk=fill['entry'],fill['risk_initial']; stop=plan['stop']; target=plan['tp1']; pip=plan['pip_size']
    out={**fill,'status':'OPEN','scored':False,'entry_at':control.iso(available),'modelled_contingent_order':True,'user_delivery_verified':False}
    previous=available;best=entry;actions=[];pending_exit=None
    for index,bar in enumerate(complete):
        if bar['t']-previous>timedelta(minutes=15,seconds=1) and not expected_weekend_gap(previous,bar['t']):
            return {**out,'status':'DATA_GAP','reason':'MISSING_LIVE_SESSION_BAR','actions':actions}
        previous=bar['t'];q=bar['bid'] if side>0 else bar['ask'];end=bar['t']+timedelta(minutes=15)
        stopped=(q['l']<=stop if side>0 else q['h']>=stop)
        targeted=(q['h']>=target if side>0 else q['l']<=target)
        # Both extremes may occur in either order; never choose the winner.
        raw=None;reason=None
        if (q['o']<=stop if side>0 else q['o']>=stop):raw=q['o'];reason='STOP_GAP'
        elif (q['o']>=target if side>0 else q['o']<=target):raw=target;reason='TARGET'
        elif pending_exit:raw=q['o'];reason=pending_exit
        elif stopped and targeted:return {**out,'status':'AMBIGUOUS','scored':False,'at':control.iso(end),'actions':actions}
        elif stopped:raw=stop;reason='STOP'
        elif targeted:raw=target;reason='TARGET'
        if raw is not None:
            exit_=raw-side*SLIP_PIPS*pip; net=side*(exit_-entry)/risk
            return {**out,'status':'CLOSED','scored':True,'exit':exit_,'at':control.iso(end),'reason':reason,
                    'net_r_after_slippage':net,'net_r_extra_cost_stress':net-pip/risk-entry*.05*max(0,(end-available).total_seconds())/(365*86400*risk),
                    'financing_verified':False,'actions':actions}
        if index>=95:pending_exit='24_HOUR_BAR_LIMIT'
        elif protective and index>=15 and side*(q['c']-entry)<.5*risk:pending_exit='FOUR_HOUR_PROGRESS_EXIT'
        # Decide from this COMPLETED close; the tightened stop applies NEXT bar.
        best=max(best,q['c']) if side>0 else min(best,q['c'])
        if protective and side*(best-entry)>=risk:
            breakeven=entry+side*SLIP_PIPS*pip;trail=best-side*1.5*plan['atr_at_decision']
            proposed=max(stop,breakeven,trail) if side>0 else min(stop,breakeven,trail)
            if proposed!=stop:actions.append({'decided_at':control.iso(end),'effective':'NEXT_BAR','stop':proposed})
            stop=proposed
    return {**out,'last_mark_at':control.iso(complete[-1]['t']+timedelta(minutes=15)),
            'mark':(complete[-1]['bid'] if side>0 else complete[-1]['ask'])['c'],'actions':actions}


def apply_caps(book):
    """Apply caps using exposure at the entry time, not a later profitable exit."""
    for arm in ARMS:
        accepted=[]
        candidates=[]
        for key,row in book['records'].items():
            result=row.get('results',{}).get(arm,{})
            underlying=row.get('blocked_counterfactual',{}).get(arm,result)
            if underlying.get('entry_at'):candidates.append((underlying['entry_at'],key,row,underlying))
        for entered,key,row,result in sorted(candidates,key=lambda r:(r[0],r[1])):
            at=control.when(entered);pair=row['plan']['instrument']
            active=[r for r in accepted if r['closed'] is None or r['closed']>at]
            currencies=set(pair.split('_'))
            reason=('THREE_POSITION_CAP' if len(active)>=3 else
                    'DUPLICATE_OPEN_PAIR' if any(r['pair']==pair for r in active) else
                    'TWO_PER_CURRENCY_CAP' if any(sum(c in r['pair'].split('_') for r in active)>=2 for c in currencies) else None)
            if reason:
                row.setdefault('blocked_counterfactual',{})[arm]=result
                row['results'][arm]={'status':'PORTFOLIO_BLOCKED','scored':False,'reason':reason,'entry_candidate_at':entered}
            else:
                row['results'][arm]=result
                closed=control.when(result.get('at')) if result.get('scored') or result.get('status')=='AMBIGUOUS' else None
                accepted.append({'pair':pair,'closed':closed})


def metrics(book):
    rows=list(book['records'].values());out={}
    for arm in ARMS:
        results=[r.get('results',{}).get(arm,{}) for r in rows]
        scored=sorted((r for r in results if r.get('scored') is True),key=lambda r:r.get('at',''))
        values=[r.get('net_r_after_slippage',r.get('net_r_stressed_illustrative')) for r in scored]
        values=[x for x in values if isinstance(x,(int,float)) and math.isfinite(x)]
        total=peak=dd=0
        for value in values:total+=value;peak=max(peak,total);dd=max(dd,peak-total)
        out[arm]={'outcomes':dict(Counter(r.get('status','NOT_EVALUATED') for r in results)),
                  'fills':sum(bool(r.get('entry_at')) for r in results),'scored':len(values),
                  'net_r':sum(values) if values else None,'mean_net_r':sum(values)/len(values) if values else None,
                  'max_drawdown_r':dd if values else None,
                  'scored_london_days':len({control.when(r['at']).astimezone(ZoneInfo('Europe/London')).date().isoformat() for r in scored if control.when(r.get('at'))}),
                  'extra_cost_stress_total_r':sum(r['net_r_extra_cost_stress'] for r in scored if 'net_r_extra_cost_stress' in r) if scored and all('net_r_extra_cost_stress' in r for r in scored) else None}
    matched=[]
    for row in rows:
        a=row.get('results',{}).get(ARMS[1],{});b=row.get('results',{}).get(ARMS[2],{})
        if a.get('scored') and b.get('scored'):
            matched.append(b['net_r_after_slippage']-a['net_r_after_slippage'])
    return {'arms':out,'paired_exit_cases':len(matched),'paired_exit_improvement_r':sum(matched) if matched else None,
            'profitability_established':False,'automatic_promotion_allowed':False,
            'minimum_review':'100 independent pair-days across20 London days, positive stressed expectancy and independent review; sample floors do not prove an edge.'}


def update(summary,news,book=None,fetch=None,now=None):
    now=now or datetime.now(UTC);version_hash=digest()
    if book is None:
        book={'schema':1,'version':VERSION,'rule_sha256':version_hash,'study_started_at':control.iso(now),'records':{},'observations':{}}
    if book.get('version')!=VERSION or book.get('rule_sha256')!=version_hash:raise ValueError('EXECUTION_STUDY_RULES_CHANGED')
    source=control.when(summary.get('collected_at'))
    if source and source>=control.when(book['study_started_at']) and timedelta(0)<=now-source<=timedelta(minutes=15):
        for pair in parent.PAIRS:
            q=((summary.get('pairs') or {}).get(pair) or {}).get('quote') or {}
            qt=control.when(q.get('time'))
            if qt and q.get('tradeable') is True and timedelta(0)<=now-qt<=timedelta(minutes=5):
                stamp=control.iso(qt);book['observations'].setdefault(pair,[])
                if stamp not in book['observations'][pair]:book['observations'][pair].append(stamp)
        proposals=parent.propose(summary,news,now) if now.weekday()<5 and 7<=now.hour<17 else []
        for plan in proposals:
            pair=plan['instrument'];stamp=control.iso(now)
            quote=((summary.get('pairs') or {}).get(pair) or {}).get('quote') or {}
            quote_at=control.when(quote.get('time'))
            if quote_at is None or quote_at>now:continue
            # One immutable setup per pair/M15bar; repeated scans cannot mint duplicate exposure.
            key=VERSION+'-'+pair+'-'+plan['last_source_bar_at']
            if key in book['records']:continue
            plan={**plan,'id':key,'policy':VERSION,'created_at':stamp,'study_started_at':book['study_started_at'],
                  'source_setup_version':parent.VERSION,'execution_test_only':True,
                  'event_blackouts_utc':[{'start':control.iso(control.when(e['time'])-timedelta(minutes=30)),
                                         'end':control.iso(control.when(e['time'])+timedelta(minutes=60))}
                    for e in (news.get('calendar') or {}).get('events',[])
                    if e.get('impact')=='high' and e.get('currency') in pair.split('_') and control.when(e.get('time'))]}
            book['records'][key]={'plan':plan,'plan_sha256':hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest(),'registered_at':stamp,'results':{},'last_checked_at':None}
    pending=[r for r in book['records'].values() if any(r.get('results',{}).get(a,{}).get('status') not in
             {'CLOSED','ENTRY_REJECTED','EXPIRED_UNFILLED','AMBIGUOUS','WIN_TP1','LOSS_STOP','TIME_EXIT','MISSED_ENTRY','INVALIDATED_PRE_ENTRY','EXPIRED_UNTRIGGERED','PORTFOLIO_BLOCKED'} for a in ARMS)]
    for row in sorted(pending,key=lambda r:(r.get('last_checked_at') or '',r['registered_at']))[:MAX_REPLAYS]:
        p=row['plan']
        if row.get('plan_sha256')!=hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest():raise ValueError('IMMUTABLE_SETUP_CHANGED')
        try:
            bars=fetch(p,now) if fetch else prices.source_bars(p,now)
            evidence=getattr(bars,'evidence_path',None)
            for arm in ARMS:
                prior=row['results'].get(arm,{})
                if prior.get('scored') is True or prior.get('status') in {'ENTRY_REJECTED','EXPIRED_UNFILLED','AMBIGUOUS','PORTFOLIO_BLOCKED','WIN_TP1','LOSS_STOP','TIME_EXIT','MISSED_ENTRY','INVALIDATED_PRE_ENTRY','EXPIRED_UNTRIGGERED'}:continue
                result=(control.replay(p,bars,now,[control.when(o) for o in book['observations'].get(p['instrument'],[])])
                        if arm==ARMS[0] else replay(p,bars,now,arm==ARMS[2]))
                if evidence:result['source_evidence']=evidence
                evidence_state=row.setdefault('arm_evidence',{}).setdefault(arm,{})
                evidence_state['result']=prior
                row['results'][arm]=control.reconcile(evidence_state,result,control.iso(now))
                evidence_state.pop('result',None)
            row.pop('source_error',None)
        except (OSError,RuntimeError,TypeError,ValueError,KeyError,TimeoutError) as exc:
            row['source_error']=type(exc).__name__  # preserve prior open/scored evidence
        row['last_checked_at']=control.iso(now)
    apply_caps(book)
    book['checked_at']=control.iso(now);book['metrics']=metrics(book)
    book['no_broker_orders']=True;book['risk_policy']={'planned_fraction_per_model_trade':.0025,'maximum_open':3,'maximum_per_currency':2,'account_equity_verified':False,'sizing_note':'Normalized planned-risk comparisons; no GBP stake or broker-account profit inferred.'};book['mode']='PROSPECTIVE_PAPER_EXECUTION_EXPERIMENT_NOT_MANUAL_ENTRY_APPROVAL'
    book['limitations']=['Next-bar fills are contingent-order model assumptions, not timely user delivery or broker fills.',
        'Inherited contextual news may be incomplete; uncertainty is retained and known event blackouts remain.',
        'Bid/ask and adverse slippage included; actual historical broker funding/fees unverified.',
        'Original v21, simple/liquid cohorts and prior results unchanged. No retrospective entries imported.']
    return book


def main():
    if len(sys.argv)>2 or len(sys.argv)==2 and sys.argv[1]!='--fast':raise SystemExit('Use optional --fast')
    fast=len(sys.argv)==2;now=datetime.now(UTC);book=json.loads(OUT.read_text()) if OUT.exists() else None
    summary=json.loads((ROOT/('data/fast_latest.json' if fast else 'data/summary.json')).read_text());news=json.loads((ROOT/('data/fast_news_latest.json' if fast else 'data/news_latest.json')).read_text())
    result=update(summary,news,book,now=now);result['last_workflow_run_id']=os.environ.get('GITHUB_RUN_ID');OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print('Confirmed-momentum execution PAPER ONLY:',json.dumps(result['metrics']))
if __name__=='__main__':main()

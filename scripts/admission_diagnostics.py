"""Independent full-gate observations; never waive a gate or create a signal.

First-failure counts cannot establish which filter is binding. Evaluate all
observable conditions on the unmodified source snapshot, retaining unknowns.
Economic feasibility is a diagnostic, not a trade even if other gates fail.
"""
from __future__ import annotations
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
try:
    from scripts.signal_admission import moment, number, trend, news_gate, qualify, EXCLUDED
    from scripts.adaptive_research import entry_context
except ModuleNotFoundError:
    from signal_admission import moment, number, trend, news_gate, qualify, EXCLUDED
    from adaptive_research import entry_context
ROOT=Path(__file__).resolve().parents[1]

def observe(pair, ranked, source, news, now, route):
    direction=ranked.get('deep_entry_scout_direction') or ranked.get('potential_direction_not_approved')
    q,frames=source.get('quote') or {},source.get('frames') or {}
    m=(frames.get('M15') or {}).get('indicators') or {}
    h=(frames.get('H1') or {}).get('indicators') or {}
    bid,ask,pip,spread=[number(q.get(k)) for k in ('bid','ask','pip_size','spread_pips')]
    atr=number(m.get('atr14'));qt=moment(q.get('time'))
    check={}
    check['pair_supported']=pair not in EXCLUDED
    check['direction_known']=direction in ('LONG','SHORT')
    check['quote_timely_tradeable']=bool(qt and q.get('tradeable') is True and timedelta(seconds=-180)<=now-qt<=timedelta(minutes=20))
    valid=bool(None not in (bid,ask,pip,spread) and 0<bid<ask and pip>0 and spread>0)
    check['valid_two_sided_quote']=valid
    check['absolute_spread_cap']=spread<=10 if spread is not None else None
    check['relative_spread_cap']=spread*pip<=.5*atr if valid and atr and atr>0 else None
    for tf in ('M15','H1','H4','D'):
        observed=trend((frames.get(tf) or {}).get('indicators') or {})
        check['trend_'+tf]=observed==direction if observed and direction in ('LONG','SHORT') else None
    for tf,ind in (('M15',m),('H1',h)):
        adx=number(ind.get('adx14'));plus=number(ind.get('plus_di14'));minus=number(ind.get('minus_di14'))
        check['adx_'+tf]=adx>=20 if adx is not None else None
        check['dmi_'+tf]=((plus>minus) if direction=='LONG' else minus>plus) if None not in (plus,minus) and direction in ('LONG','SHORT') else None
    context=entry_context({'quote':q,'frames':frames},direction)
    check['no_reversal_or_exhaustion']=not context['block_new_alert'] if context['status']!='UNVERIFIABLE' else None
    bars=(frames.get('M15') or {}).get('last_3_completed_candles') or []
    opened=moment(bars[-1].get('time')) if bars else None
    check['recent_completed_history']=bool(len(bars)>=3 and opened and opened+timedelta(minutes=15)<=now and now-opened-timedelta(minutes=15)<=timedelta(minutes=30))
    check['news_gate']=news_gate(pair,news,now) is None
    economics=None
    try:
        mids=[b['mid'] for b in bars[-3:]]
        last=mids[-1];o,c,hi,lo=[float(last[k]) for k in ('o','c','h','l')]
        span=hi-lo;body=abs(c-o)
        long=direction=='LONG';side=ask if long else bid;ema=number(m.get('ema20'))
        if not valid or not atr or atr<=0 or not ema or len(mids)<3 or direction not in ('LONG','SHORT'):
            raise ValueError('Unavailable route economics')
        if route=='MOMENTUM_CONTINUATION':
            strength=((c-lo) if long else (hi-c))/span if span>0 else 0
            check['route_pattern']=span>0 and body/span>=.55 and strength>=.72 and (c>o if long else c<o)
            check['route_not_stretched']=abs(side-c)<=.4*atr
            entry=(hi+pip+spread*pip) if long else (lo-pip-spread*pip)
        else:
            check['route_pattern']=True  # Future completed rejection still mandatory.
            check['route_not_stretched']=abs(side-ema)<=1.5*atr
            entry=ema
        swing_low=min(float(b['l']) for b in mids);swing_high=max(float(b['h']) for b in mids)
        stop=min(swing_low-.12*atr,entry-1.05*atr) if long else max(swing_high+.12*atr,entry+1.05*atr)
        target_key='resistance20' if long else 'support20'
        levels=[number(((frames.get(tf) or {}).get('indicators') or {}).get(target_key)) for tf in ('M15','H1','H4','D')]
        targets=sorted({x for x in levels if x is not None and (x>entry if long else x<entry)},reverse=not long)
        risk=abs(entry-stop);check['structural_stop_range']=.75*atr<=risk<=3.5*atr
        check['observed_target_available']=bool(targets)
        if targets:
            target=targets[0];reward=abs(target-entry);cost=min(10,spread*1.15)*pip
            gross=reward/risk;net=(reward-cost)/(risk+cost)
            check['gross_reward_minimum']=gross>=1.5;check['net_reward_minimum']=net>=1.35
            economics={'entry_reference':entry,'stop_reference':stop,'nearest_target':target,
                       'gross_rr':round(gross,5),'net_rr':round(net,5)}
    except (KeyError,IndexError,ValueError,TypeError,ZeroDivisionError):
        for key in ('route_pattern','route_not_stretched','structural_stop_range','observed_target_available','gross_reward_minimum','net_reward_minimum'):
            check.setdefault(key,None)
    for key in ('route_pattern','route_not_stretched','structural_stop_range','observed_target_available','gross_reward_minimum','net_reward_minimum'):
        check.setdefault(key,None)
    plan,reason=qualify(pair,ranked,source,news,now,route)
    return {'pair':pair,'direction':direction,'route':route,'checks':check,
            'failed':[k for k,v in check.items() if v is False],
            'unknown':[k for k,v in check.items() if v is None],
            'economic_feasibility_only':economics,'canonical_first_rejection':reason,'news_reason':news_gate(pair,news,now),
            'canonical_qualified':bool(plan),'not_a_signal':True}

def audit(summary,news):
    now=moment(summary.get('collected_at'))
    if not now or summary.get('status') not in ('OK','PARTIAL'):
        return {'source_at':summary.get('collected_at'),'status':'SOURCE_UNAVAILABLE','rows':[]}
    rows=[observe(r['instrument'],r,(summary.get('pairs') or {}).get(r['instrument']) or {},news,now,route)
          for r in summary.get('ranked_review_candidates') or [] if r.get('instrument')
          for route in ('MOMENTUM_CONTINUATION','EMA20_PULLBACK')]
    gates=sorted({k for r in rows for k in r['checks']})
    rates={k:{'pass':sum(r['checks'].get(k) is True for r in rows),
              'fail':sum(r['checks'].get(k) is False for r in rows),
              'unknown':sum(r['checks'].get(k) is None for r in rows)} for k in gates}
    feasible=[r for r in rows if all(r['checks'].get(k) is True for k in
              ('valid_two_sided_quote','absolute_spread_cap','relative_spread_cap','recent_completed_history',
               'route_pattern','route_not_stretched','structural_stop_range','observed_target_available',
               'gross_reward_minimum','net_reward_minimum'))]
    return {'source_at':summary['collected_at'],'status':'DIAGNOSTICS_ONLY','row_count':len(rows),
        'gate_counts':rates,'economically_feasible_route_observations':len(feasible),
        'feasible_but_not_admitted':[{ 'pair':r['pair'],'route':r['route'],
             'failed':r['failed'],'unknown':r['unknown']} for r in feasible if not r['canonical_qualified']],
        'canonical_first_rejections':dict(Counter(r['canonical_first_rejection'] for r in rows if r['canonical_first_rejection'])),
        'method':'Unmodified contemporaneous inputs. Counts overlap and are pair-route observations, not independent trades. Economics do not establish a confirmation, future fill or predictive edge.',
        'rows':rows}

def main():
    result=audit(json.loads((ROOT/'data/summary.json').read_text()),json.loads((ROOT/'data/news_latest.json').read_text()))
    (ROOT/'data/admission_full_gate_audit.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print('FX full-gate diagnostics',result['status'],'economically feasible',result.get('economically_feasible_route_observations'))

if __name__=='__main__':main()

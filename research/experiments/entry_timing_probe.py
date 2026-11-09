"""Reproduce the rejected entry-timing diagnostics on saved OANDA evidence.

Run explicitly; this file is not part of any trading or scheduled execution path.
Never promotes a strategy or edits the existing paper ledger.
"""
import json,sys,collections,argparse,hashlib
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import paper_trade_audit as old
UTC=timezone.utc
parser=argparse.ArgumentParser(description='Offline diagnostic only; never changes entries, ledgers or orders.')
parser.add_argument('--mode',choices=['confirmed_next_open','unconfirmed_next_open','unconfirmed_with_floor'],default='confirmed_next_open')
parser.add_argument('--period',choices=['development','retrospective_reserved'],default='development')
args=parser.parse_args()
split=datetime(2026,11,5,7,tzinfo=UTC)
cut=split if args.period=='development' else datetime.now(UTC)
root=ROOT
rows=json.load(open(root/'data/strategy_comparison.json'))['records'];selected={}
for r in rows:
 p=r.get('plan');
 if p and old.stamp(p['created_at'])<cut and (args.period=='development' or old.stamp(p['created_at'])>=split):selected.setdefault(p['id'],r)
# Establish all choices before evaluating: identical original trigger, next M15
# executable open, unchanged stop/target, original cost gates, .25pip/side.
def confirmed(p,bars):
 created=old.stamp(p['created_at']);expiry=old.stamp(p['expires_at']);prev=None;pending=None
 for b in bars:
  close=b['t']+timedelta(minutes=15)
  if close<=created:prev=b['mid']['c'];continue
  if b['t']<created:continue
  if close>expiry:break
  if old._stop_hit(p,b):return None
  if old.blackout(p,close,b['t']):prev=b['mid']['c'];pending=None;continue
  route='B' if old._eligible_trigger(p,b) else None
  if p.get('alert_a') is not None:
   a=float(p['alert_a']);tol=2*old.pip_size(p);long=p['direction']=='LONG'
   if pending:
    age=close-pending;tested=b['mid']['l']<=a+tol and b['mid']['c']>a if long else b['mid']['h']>=a-tol and b['mid']['c']<a
    if timedelta(0)<age<=timedelta(minutes=60) and tested:route='A'
    if age>timedelta(minutes=60) or (b['mid']['c']<a-tol if long else b['mid']['c']>a+tol):pending=None
   if prev is not None and (prev<=a<b['mid']['c'] if long else prev>=a>b['mid']['c']):pending=close
  if route is None and old._eligible_continuation(p,b,prev):route='C'
  prev=b['mid']['c']
  if route:return close
 return None
results=[]
for id,row in selected.items():
 p=row['plan'];path=(row.get('result')or{}).get('source_evidence');base={'id':id,'pair':p['instrument'],'created_at':p['created_at'],'original_status':row.get('result',{}).get('status')}
 if not path or not (root/path).exists():results.append({**base,'status':'NO_SOURCE'});continue
 bars=old.parse_bars(json.load(open(root/path)));bars=[b for b in bars if b['t']+timedelta(minutes=15)<=cut]
 trigger=confirmed(p,bars)
 if args.mode!='confirmed_next_open':
  trigger=old.stamp(p['created_at']);trigger=trigger.replace(minute=trigger.minute//15*15,second=0,microsecond=0)+timedelta(minutes=15)
 if trigger is None:results.append({**base,'status':'NO_CONFIRMATION'});continue
 first=next((i for i,b in enumerate(bars) if b['t']==trigger),None)
 if first is None:results.append({**base,'status':'ENTRY_DATA_GAP'});continue
 b=bars[first];sgn=1 if p['direction']=='LONG' else -1;pip=old.pip_size(p);slip=.25*pip;entry=b['ask']['o']+slip if sgn>0 else b['bid']['o']-slip;stop=float(p['stop']);tp=float(p['tp1']);spread=b['ask']['o']-b['bid']['o'];risk=abs(entry-stop);reward=sgn*(tp-entry)
 atr=float((p.get('technical_context') or {}).get('m15_atr') or 0)
 if (args.mode=='unconfirmed_with_floor' and not(atr>0 and abs(entry-float(p['planned_entry']))<=.4*atr and risk>=.75*atr)) or not(sgn*(entry-stop)>0 and reward>0) or spread/pip>p['max_oanda_spread_pips'] or reward/risk<1.5 or (reward-spread)/(risk+spread)<1.35:
  results.append({**base,'status':'COST_OR_GEOMETRY'});continue
 arm={}
 for protective in [False,True]:
  stop0=stop;prior=trigger;result={'status':'OPEN'}
  for j,b in enumerate(bars[first:]):
   if b['t']-prior>timedelta(minutes=15,seconds=1):result={'status':'GAP'};break
   prior=b['t'];x=b['bid']if sgn>0 else b['ask'];stopped=x['l']<=stop0 if sgn>0 else x['h']>=stop0;target=x['h']>=tp if sgn>0 else x['l']<=tp
   if stopped and target:result={'status':'AMBIGUOUS'};break
   if stopped or target or j>=95:
    raw=(min(stop0,x['o']) if sgn>0 else max(stop0,x['o'])) if stopped else tp if target else x['c'];exit=raw-sgn*slip
    result={'status':'CLOSED','r':sgn*(exit-entry)/risk,'exit_at':old.iso(b['t']+timedelta(minutes=15))};break
   if protective and sgn*(x['c']-entry)>=risk:stop0=max(stop0,entry+slip)if sgn>0 else min(stop0,entry-slip)
  arm['protect'if protective else 'fixed']=result
 results.append({**base,'status':'FILLED_MODEL','arms':arm})
print(json.dumps({'scope':'RETROSPECTIVE_DIAGNOSTIC_NOT_PORTFOLIO_RETURN_OR_FORWARD_VALIDATION','mode':args.mode,'period':args.period,'source_ledger_sha256':hashlib.sha256((root/'data/strategy_comparison.json').read_bytes()).hexdigest(),'cutoff':old.iso(cut),'selection':'All distinct registered plans in date interval, first preserved policy per immutable ID; no selection by outcome. Unknown cases remain counted.','limitations':['Unconfirmed entry variants are rejected diagnostics, not approved trading rules.','Cases may overlap currency exposures and are not independent portfolio trades.','No full historical news reconstruction, actual broker funding or verified human execution.','Reserved-period data existed before this experiment and are not a newly untouched holdout.'],'counts':dict(collections.Counter(r['status']for r in results)),'arms':{a:{'n':sum(r.get('arms',{}).get(a,{}).get('status')=='CLOSED'for r in results),'netR':sum(r.get('arms',{}).get(a,{}).get('r',0)for r in results)}for a in ['fixed','protect']},'rows':results},indent=2))

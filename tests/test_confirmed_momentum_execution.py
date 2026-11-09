import copy
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from scripts import confirmed_momentum_execution as study
UTC=timezone.utc
T=datetime(2026,11,10,10,0,tzinfo=UTC)

def plan():
 return {'id':'x','instrument':'EUR_USD','direction':'LONG','created_at':'2026-11-10T10:01:00Z','expires_at':'2026-11-10T11:31:00Z','pip_size':.0001,'atr_at_decision':.001,'indicative_entry':1.1001,'stop':1.098,'tp1':1.1055,'max_spread_pips':2.5,'event_blackouts_utc':[]}
def bar(i,o=1.1001,h=1.1008,l=1.1,c=1.1004):
 bid={k:v for k,v in zip(('o','h','l','c'),(o,h,l,c))};ask={k:v+.0001 for k,v in bid.items()}
 return {'t':T+timedelta(minutes=15*i),'bid':bid,'ask':ask,'mid':{k:(bid[k]+ask[k])/2 for k in bid}}

class ConfirmedMomentumTests(unittest.TestCase):
 def test_fill_requires_future_bar_not_registration_bar(self):
  r=study.replay(plan(),[bar(0)],T+timedelta(minutes=15));self.assertEqual(r['status'],'PENDING_ENTRY')
  r=study.replay(plan(),[bar(1)],T+timedelta(minutes=30));self.assertEqual(r['entry_at'],'2026-11-10T10:15:00Z')
 def test_missing_planned_bar_does_not_substitute_later_winner(self):
  self.assertEqual(study.replay(plan(),[bar(2)],T+timedelta(hours=1))['status'],'DATA_GAP')
 def test_actual_cost_floor_and_stop_inside_quote_block(self):
  p=plan();p['stop']=1.10015
  self.assertEqual(study.entry_check(p,bar(1))[1],'STOP_ALREADY_INSIDE_EXIT_SIDE_QUOTE')
  p=plan();p['stop']=1.0998
  self.assertEqual(study.entry_check(p,bar(1))[1],'STOP_TOO_CLOSE_RELATIVE_TO_VOLATILITY')
 def test_adverse_slippage_and_price_chasing(self):
  x,error=study.entry_check(plan(),bar(1));self.assertIsNone(error);self.assertGreater(x['entry'],bar(1)['ask']['o'])
  self.assertEqual(study.entry_check(plan(),bar(1,1.101,1.102,1.1008,1.1015))[1],'PRICE_CHASING')
 def test_target_profit_is_after_exit_slippage(self):
  r=study.replay(plan(),[bar(1),bar(2,1.101,1.106,1.1008,1.105)],T+timedelta(hours=1))
  self.assertTrue(r['scored']);self.assertEqual(r['reason'],'TARGET');self.assertLess(r['exit'],plan()['tp1']);self.assertLess(r['net_r_extra_cost_stress'],r['net_r_after_slippage'])
 def test_ambiguous_bar_cannot_be_scored_as_win(self):
  r=study.replay(plan(),[bar(1),bar(2,1.1001,1.106,1.097,1.1)],T+timedelta(hours=1))
  self.assertEqual(r['status'],'AMBIGUOUS');self.assertFalse(r['scored'])
 def test_gap_stop_executes_at_worse_open(self):
  r=study.replay(plan(),[bar(1),bar(2,1.095,1.106,1.094,1.1)],T+timedelta(hours=1))
  self.assertEqual(r['reason'],'STOP_GAP');self.assertLess(r['exit'],1.095)
 def test_profit_stop_applies_next_bar_not_earlier_low(self):
  bars=[bar(1),bar(2,1.1004,1.103,1.1003,1.1027)]
  r=study.replay(plan(),bars,T+timedelta(hours=1),True);self.assertEqual(r['status'],'OPEN');self.assertTrue(r['actions'])
  bars.append(bar(3,1.102,1.1025,1.1001,1.101))
  r=study.replay(plan(),bars,T+timedelta(hours=2),True);self.assertTrue(r['scored']);self.assertGreaterEqual(r['net_r_after_slippage'],0)
 def test_blackout_is_frozen_at_registration(self):
  p=plan();p['event_blackouts_utc']=[{'start':'2026-11-10T10:10:00Z','end':'2026-11-10T10:30:00Z'}]
  self.assertEqual(study.replay(p,[bar(1)],T+timedelta(hours=1))['reason'],'FROZEN_EVENT_BLACKOUT')
 def test_caps_use_entry_time_even_after_later_closures(self):
  rows={}
  for i,pair in enumerate(['EUR_USD','GBP_JPY','AUD_CAD','NZD_CHF']):
   p=plan();p['instrument']=pair
   result={'entry_at':'2026-11-10T10:15:00Z','status':'CLOSED','scored':True,'at':'2026-11-10T11:15:00Z','net_r_after_slippage':1}
   rows[str(i)]={'plan':p,'results':{a:copy.deepcopy(result) for a in study.ARMS}}
  book={'records':rows};study.apply_caps(book);self.assertEqual(rows['3']['results'][study.ARMS[1]]['status'],'PORTFOLIO_BLOCKED')
 def test_stale_source_cannot_import_old_plans_as_new_forward_evidence(self):
  summary={'collected_at':'2026-11-09T10:00:00Z'}
  with patch.object(study.parent,'propose') as propose:
   b=study.update(summary,{},now=T);propose.assert_not_called();self.assertEqual(b['records'],{})
 def test_changed_study_cannot_continue_old_results(self):
  b=study.update({}, {},now=T);b['rule_sha256']='wrong'
  with self.assertRaisesRegex(ValueError,'RULES_CHANGED'):study.update({}, {},b,now=T)

class AdditionalExecutionTests(unittest.TestCase):
 def test_short_target_uses_ask_side_and_adverse_exit_slippage(self):
  p=plan();p.update(direction='SHORT',indicative_entry=1.0999,stop=1.102,tp1=1.0945)
  def flip(b):
   out={'t':b['t']}
   for side,old in [('bid','ask'),('ask','bid'),('mid','mid')]:
    x=b[old];out[side]={'o':2.2-x['o'],'h':2.2-x['l'],'l':2.2-x['h'],'c':2.2-x['c']}
   return out
  r=study.replay(p,[flip(bar(1)),flip(bar(2,1.101,1.106,1.1008,1.105))],T+timedelta(hours=1))
  self.assertTrue(r['scored']);self.assertEqual(r['reason'],'TARGET');self.assertGreater(r['exit'],p['tp1'])
 def test_progress_exit_waits_for_next_open(self):
  bars=[bar(i) for i in range(1,17)]
  r=study.replay(plan(),bars,T+timedelta(minutes=17*15),True);self.assertEqual(r['status'],'OPEN')
  bars.append(bar(17,1.1002,1.1008,1.1001,1.1004))
  r=study.replay(plan(),bars,T+timedelta(minutes=18*15),True);self.assertEqual(r['reason'],'FOUR_HOUR_PROGRESS_EXIT')
 def test_live_session_gap_is_not_assumed_weekend(self):
  self.assertFalse(study.expected_weekend_gap(T,T+timedelta(minutes=30)))

class ForwardRegistrationTests(unittest.TestCase):
 def test_repeated_same_setup_keeps_original_registration_and_economics(self):
  now=T+timedelta(minutes=1);p=plan();p['last_source_bar_at']='2026-11-10T09:45:00Z'
  summary={'status':'OK','collected_at':study.control.iso(now),'pairs':{'EUR_USD':{'quote':{'time':study.control.iso(now),'tradeable':True}}}}
  book=study.update({}, {},now=T)
  with patch.object(study.parent,'propose',return_value=[p]):
   book=study.update(summary,{},book,fetch=lambda p,t:[],now=now)
   before=copy.deepcopy(next(iter(book['records'].values()))['plan'])
   book=study.update(summary,{},book,fetch=lambda p,t:[],now=now+timedelta(minutes=1))
  self.assertEqual(len(book['records']),1);self.assertEqual(next(iter(book['records'].values()))['plan'],before)
 def test_source_error_preserves_an_open_result(self):
  now=T+timedelta(minutes=1);p=plan();p['last_source_bar_at']='2026-11-10T09:45:00Z'
  summary={'status':'OK','collected_at':study.control.iso(now),'pairs':{'EUR_USD':{'quote':{'time':study.control.iso(now),'tradeable':True}}}}
  book=study.update({}, {},now=T)
  with patch.object(study.parent,'propose',return_value=[p]):book=study.update(summary,{},book,fetch=lambda p,t:[],now=now)
  row=next(iter(book['records'].values()));row['results'][study.ARMS[1]]={'status':'OPEN','entry_at':'2026-11-10T10:15:00Z','entry':1.1002,'scored':False}
  before=copy.deepcopy(row['results'][study.ARMS[1]])
  def fail(p,t):raise RuntimeError('Provider unavailable')
  book=study.update({}, {},book,fetch=fail,now=now+timedelta(minutes=30))
  self.assertEqual(next(iter(book['records'].values()))['results'][study.ARMS[1]],before)

class RetainedExposureTests(unittest.TestCase):
 def test_rolloff_keeps_proven_entry_as_open_exposure(self):
  now=T+timedelta(minutes=1);p=plan();p['last_source_bar_at']='2026-11-10T09:45:00Z'
  summary={'status':'OK','collected_at':study.control.iso(now),'pairs':{'EUR_USD':{'quote':{'time':study.control.iso(now),'tradeable':True}}}}
  book=study.update({}, {},now=T)
  with patch.object(study.parent,'propose',return_value=[p]):book=study.update(summary,{},book,fetch=lambda p,t:[],now=now)
  row=next(iter(book['records'].values()));row['results'][study.ARMS[1]]={'status':'OPEN','entry_at':'2026-11-10T10:15:00Z','entry':1.100225,'scored':False}
  book=study.update({}, {},book,fetch=lambda p,t:[bar(3)],now=T+timedelta(hours=1))
  result=next(iter(book['records'].values()))['results'][study.ARMS[1]]
  self.assertEqual(result['status'],'OPEN');self.assertEqual(result['entry'],1.100225);self.assertEqual(result['data_status'],'REPLAY_UNVERIFIABLE')
 def test_economic_terms_cannot_be_edited_under_same_id(self):
  now=T+timedelta(minutes=1);p=plan();p['last_source_bar_at']='2026-11-10T09:45:00Z'
  summary={'status':'OK','collected_at':study.control.iso(now),'pairs':{'EUR_USD':{'quote':{'time':study.control.iso(now),'tradeable':True}}}}
  book=study.update({}, {},now=T)
  with patch.object(study.parent,'propose',return_value=[p]):book=study.update(summary,{},book,fetch=lambda p,t:[],now=now)
  next(iter(book['records'].values()))['plan']['stop']=1.0
  with self.assertRaisesRegex(ValueError,'IMMUTABLE_SETUP_CHANGED'):study.update({}, {},book,fetch=lambda p,t:[],now=T+timedelta(hours=1))

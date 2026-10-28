"""Regression: prospective chronology, shared gates, study isolation and audit state."""
import unittest
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import signal_watch as watch
from scripts.signal_admission import qualify
from scripts.strategy_comparison import freeze, update
from scripts.missed_entry_audit import audit
from scripts.fx_universe import deep_selection
from test_signal_admission import source, ranked, news, NOW
from test_continuation_route import sample, NOW as WATCH_NOW
from test_signal_continuation_paper import plan, candles, NOW as REPLAY_NOW
from scripts.paper_trade_audit import replay, _eligible_continuation

class CriticalDiagnosticTests(unittest.TestCase):
    def test_missing_frame_is_not_opposing_trend(self):
        s=source();del s['frames']['H4']
        p,reason=qualify('EUR_USD',ranked(),s,news(),NOW,'EMA20_PULLBACK')
        self.assertIsNone(p);self.assertEqual(reason,'REQUIRED_FRAME_HISTORY_MISSING:H4')

    def test_all_prequalified_pairs_get_deep_verification(self):
        ranks={f'PAIR{i}':{'research_priority':100-i,'deep_entry_scout_eligible':True} for i in range(20)}
        self.assertEqual(len(deep_selection(ranks,max_extra=12)),20)

    def test_precreation_partial_candle_cannot_trigger(self):
        pair,p=sample();p['created_at']=(WATCH_NOW-timedelta(minutes=5)).isoformat()
        self.assertIsNone(watch.signal_match(p,pair,{},WATCH_NOW))

    def test_current_price_must_pass_original_gross_and_net_gates(self):
        pair,p=sample();pair['quote'].update(bid=1.3388,ask=1.3389)
        # Remaining gross reward 1.387 < 1.50, though old 1.35 gate passed.
        self.assertIsNone(watch.signal_match(p,pair,{},WATCH_NOW))

    def test_frozen_admission_adx_is_not_vetoed_by_later_indicators(self):
        pair,p=sample();p['technical_context']={'admission_policy':'champion',
            'momentum_adx_minimum':20,'m15_adx':21,'h1_adx':21,
            'm15_plus_di':30,'m15_minus_di':10,'h1_plus_di':30,'h1_minus_di':10}
        pair['frames']['M15']['indicators']['adx14']=12
        self.assertEqual(watch.signal_match(p,pair,{},WATCH_NOW)[0],'C')

    def test_bid_exit_stop_not_midpoint(self):
        pair,p=sample();bar=deepcopy(pair['frames']['M15']['recent_completed_candles'][-1])
        bar['bid']={'o':1.336,'c':1.336,'l':p['stop']-.00001,'h':1.338}
        bar['ask']={k:v+.0001 for k,v in bar['bid'].items()}
        self.assertEqual(watch.setup_already_breached({'frames':{'M15':{'recent_completed_candles':[bar]}}},p,WATCH_NOW),'PLAN_STOP_LEVEL_TOUCHED_ON_M15')

    def test_precreation_extreme_does_not_invalidate(self):
        pair,p=sample();bar=deepcopy(pair['frames']['M15']['recent_completed_candles'][-1]);p['created_at']=(WATCH_NOW-timedelta(minutes=5)).isoformat()
        bar['bid']={'o':1.336,'c':1.336,'l':1.0,'h':2.0}
        self.assertIsNone(watch.setup_already_breached({'frames':{'M15':{'recent_completed_candles':[bar]}}},p,WATCH_NOW))

    def test_challenger_does_not_waive_economic_risk_or_become_live(self):
        s=source();s['frames']['D']['indicators']['ema20']=1.098
        p,error=qualify('EUR_USD',ranked(),s,news(),NOW,'EMA20_PULLBACK',policy='challenger_v1')
        self.assertIsNone(error);self.assertTrue(p['paper_only']);self.assertFalse(p['broker_verified'])
        s['frames']['M15']['indicators']['resistance20']=1.1005
        p,error=qualify('EUR_USD',ranked(),s,news(),NOW,'EMA20_PULLBACK',policy='challenger_v1')
        self.assertIsNone(p);self.assertEqual(error,'NEAREST_STRUCTURE_TARGET_REWARD_INADEQUATE')

    def test_momentum_adx_21_has_identical_admission_and_replay_minimum(self):
        p=plan();p['technical_context'].update(admission_policy='champion',momentum_adx_minimum=20,m15_adx=21,h1_adx=21)
        self.assertTrue(_eligible_continuation(p,candles()[1],candles()[0]['mid']['c']))

    def test_delayed_observation_cannot_assume_pattern_close_fill(self):
        p=plan();b=candles()
        r=replay(p,b,REPLAY_NOW,observation_times=[REPLAY_NOW])
        self.assertNotIn(r.get('status'),('WIN_TP1','LOSS_STOP'))
        self.assertFalse(r.get('scored'))
        self.assertIsNone(r.get('entry_at'))

    def test_study_deduplicates_same_source_bar_and_never_promotes(self):
        s={'status':'OK','collected_at':NOW.isoformat(),'ranked_review_candidates':[ranked()],'pairs':{'EUR_USD':source()}}
        b=update(s,news(),fetch=lambda *a:[])
        count=sum(bool(r.get('plan')) for r in b['records'])
        s['collected_at']=(NOW+timedelta(seconds=30)).isoformat()
        b=update(s,news(),b,fetch=lambda *a:[])
        self.assertEqual(sum(bool(r.get('plan')) for r in b['records']),count)
        self.assertFalse(b['promotion_allowed']);self.assertFalse(b['performance_validated'])

    def test_audit_reads_its_own_saved_terminal_results(self):
        row={'id':'x','first_seen_at':NOW.isoformat(),'entry_pattern_status':'FORWARD_CANDLE_OBSERVED_PAPER_ONLY','entry_pattern_at':NOW.isoformat(),'instrument':'EUR_USD','direction':'LONG'}
        prior={'replay_method_version':2,'historical_results':{'x':{'status':'PAPER_STOP_AFTER_FILL','scored':True}}}
        def forbidden(*a):raise AssertionError('Terminal result unexpectedly fetched again')
        r=audit({'opportunities':{'x':row}},prior,now=NOW,fetch=forbidden)
        self.assertEqual(r['historical_results']['x'],prior['historical_results']['x'])
        self.assertEqual(r['latency_audit']['verified_profitable_missed_manual_entries'],0)

class EmptySignalFreshnessTests(unittest.TestCase):
    def test_no_signal_still_persists_current_assessment_timestamp(self):
        import json,tempfile
        from unittest.mock import patch
        from scripts import signal_admission as admission
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            summary={'status':'OK','collected_at':NOW.isoformat(),'pairs':{},'ranked_review_candidates':[]}
            (root/'data/summary.json').write_text(json.dumps(summary))
            (root/'data/news_latest.json').write_text(json.dumps(news()))
            (root/'signals.json').write_text(json.dumps({'generated_at':'2020-01-01T00:00:00Z','signals':[]}))
            with patch.object(admission,'ROOT',root):admission.main()
            saved=json.loads((root/'signals.json').read_text())
            self.assertEqual(saved['signals'],[])
            self.assertEqual(saved['generated_at'],NOW.isoformat().replace('+00:00','Z'))

if __name__=='__main__': unittest.main()

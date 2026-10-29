"""Integration regressions for coverage, immutable histories and actual observation timing."""
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from scripts import oanda_data as market
from scripts import signal_admission as admission
from scripts import strategy_comparison as comparison
from scripts import export_public_dashboard as exporter
from scripts import forward_validation as validation
from test_signal_admission import NOW, source, ranked, news


class EntryPipelineRepairs(unittest.TestCase):
    def test_expired_plan_is_archived_without_mutating_the_input_archive(self):
        expired = {'id':'OLD','expires_at':(NOW-timedelta(minutes=1)).isoformat(),
                   'planned_entry':1.12345,'stop':1.12,'tp1':1.13}
        archive = {'signals':[]}
        summary = {'status':'OK','collected_at':NOW.isoformat()}
        updated, archived, _ = admission.admit(summary,news(),{'signals':[expired]},archive)
        self.assertEqual(archive, {'signals':[]})
        self.assertEqual(archived['signals'], [expired])
        self.assertEqual(updated['signals'], [])
        # This is the main() persistence condition which previously stayed false.
        self.assertNotEqual(archived.get('signals'),archive.get('signals'))

    def test_known_future_high_impact_window_is_frozen_for_watcher_and_paper_replay(self):
        n=news();n['calendar']={'events':[
            {'currency':'USD','impact':'High','time':(NOW+timedelta(minutes=80)).isoformat(),'title':'GDP'},
            {'currency':'JPY','impact':'High','time':(NOW+timedelta(minutes=80)).isoformat(),'title':'Unrelated'}]}
        plan,error=admission.qualify('EUR_USD',ranked(),source(),n,NOW,'EMA20_PULLBACK')
        self.assertIsNone(error)
        self.assertEqual(len(plan['event_blackouts_utc']),1)
        self.assertEqual(admission.moment(plan['event_blackouts_utc'][0]['start']),NOW+timedelta(minutes=35))

    def test_fast_replay_deferral_does_not_erase_existing_audited_state(self):
        s={'status':'OK','collected_at':NOW.isoformat(),'ranked_review_candidates':[ranked()],
           'pairs':{'EUR_USD':source()}}
        book=comparison.update(s,news(),fetch=lambda *a:[])
        for r in book['records']:
            if r.get('plan'):r['result']={'status':'OPEN','triggered_at':NOW.isoformat(),'entry':1.1}
        before=[deepcopy(r['result']) for r in book['records'] if r.get('plan')]
        s['collected_at']=(NOW+timedelta(seconds=5)).isoformat()
        result=comparison.update(s,news(),book,replay_pending=False)
        self.assertEqual([r['result'] for r in result['records'] if r.get('plan')],before)

    def collect_fixture(self, failed_quote=False, failed_frame=False):
        names = [f'{a}_{b}' for a in ('EUR','USD','GBP','AUD','CAD','CHF','NZD','JPY','SGD')
                 for b in ('EUR','USD','GBP','AUD','CAD','CHF','NZD','JPY','SGD') if a!=b][:68]
        s=source();frame=deepcopy(s['frames']['M15'])
        frame['recent_completed_candles']=frame['last_3_completed_candles']
        frame['completed_at']=frame['last_3_completed_candles'][-1]['time']
        frame['advanced']={}
        quotes={n:deepcopy(s['quote']) for n in names}
        final=deepcopy(quotes)
        for row in final.values():row['time']=NOW.isoformat()
        if failed_quote:final.pop(names[-1])
        captured={}
        def fetch(pair,tf,deep=True):
            if failed_frame and pair==names[-1] and tf=='H4':raise RuntimeError('test gap')
            if not deep:market.CANDLE_CACHE[(pair,tf)]=[]
            return deepcopy(frame)
        def write(path,value):captured[str(path)]=deepcopy(value)
        with tempfile.TemporaryDirectory() as temp:
            old=os.getcwd()
            try:
                os.chdir(temp)
                with patch.dict(os.environ,{'OANDA_ACCOUNT_ID':'test'}), \
                     patch.object(market,'request',return_value={'instruments':[
                         {'name':n,'type':'CURRENCY','pipLocation':-4} for n in names]}), \
                     patch.object(market,'fetch_quote_batch',side_effect=[(quotes,{}),(final,{})]), \
                     patch.object(market,'fetch_frame',side_effect=fetch), \
                     patch.object(market,'describe',side_effect=lambda *a,**k:deepcopy(frame)), \
                     patch.object(market,'currently_monitored_names',return_value=[]), \
                     patch.object(market,'write_json',side_effect=write):
                    market.collect()
            finally:os.chdir(old)
        return captured,names

    def test_every_one_of_68_screened_pairs_gets_full_core_history_and_final_quote(self):
        saved,names=self.collect_fixture()
        summary=saved['data/summary.json']
        self.assertEqual(summary['coverage']['pairs_deep_d1_h4_h1_m15_m5'],68)
        self.assertEqual(len(summary['coverage']['detailed_audit_pairs']),68)
        for pair in names:
            self.assertTrue(set(('M5','M15','H1','H4','D'))<=set(summary['pairs'][pair]['frames']))
            self.assertEqual(summary['pairs'][pair]['quote']['time'],NOW.isoformat())

    def test_history_gap_is_reported_and_final_quote_failure_cannot_remain_tradeable(self):
        saved,names=self.collect_fixture(failed_quote=True,failed_frame=True)
        summary=saved['data/summary.json']
        self.assertEqual(summary['status'],'PARTIAL')
        self.assertEqual(summary['coverage']['pairs_deep_d1_h4_h1_m15_m5'],67)
        self.assertFalse(summary['pairs'][names[-1]]['quote']['tradeable'])
        self.assertIn('D',summary['pairs'][names[-1]]['frames'])
        self.assertIn(names[-1],summary['coverage']['pairs_missing_or_partial'])

    def test_scan_of_another_pair_cannot_create_an_earlier_detected_fill(self):
        s={'status':'OK','collected_at':NOW.isoformat(),
           'ranked_review_candidates':[ranked()],'pairs':{'EUR_USD':source()}}
        first=comparison.update(s,news(),fetch=lambda *a:[])
        self.assertTrue(all(r['observation_scope']=='PAIR_SPECIFIC' for r in first['records']))
        later=NOW+timedelta(minutes=20)
        other={'status':'OK','collected_at':later.isoformat(),
               'ranked_review_candidates':[],'pairs':{'USD_JPY':{}}}
        observations=[]
        with patch.object(comparison,'replay',side_effect=lambda *a,**kw:
                          observations.append(kw['observation_times']) or {'status':'PENDING_TRIGGER','scored':False}):
            result=comparison.update(other,news(),first,fetch=lambda *a:[])
        self.assertTrue(observations)
        self.assertTrue(all(times==[NOW] for times in observations))
        self.assertEqual(result['pair_observation_times']['USD_JPY'],[comparison.iso(later)])

    def fast_inputs(self):
        hourly=NOW-timedelta(minutes=10)
        summary={'collected_at':hourly.isoformat(),'pairs':{'EUR_USD':source()}}
        recent={'pairs':deepcopy(summary['pairs'])}
        watch={'collected_at':hourly.isoformat(),'signals':[{'id':'OLD','status':'WAIT'}]}
        f={'status':'OK','collected_at':NOW.isoformat(),'pairs':{'EUR_USD':source()}}
        n={'collected_at':NOW.isoformat()}
        a={'status':'OK','collected_at':NOW.isoformat(),
           'oanda_collected_at':NOW.isoformat(),'news_collected_at':NOW.isoformat(),
           'signals':[{'id':'NEW','status':'PRICE_REASSESSMENT_ALERT_READY'}]}
        return summary,{},watch,recent,{},f,n,a

    def test_new_fast_detection_reaches_canonical_display_without_redating_entire_universe(self):
        args=self.fast_inputs();out=exporter.select_entry_observation(*args,NOW)
        self.assertEqual(out[2]['signals'][0]['id'],'NEW')
        self.assertEqual(out[2]['observation_stream'],'FAST_SHORTLIST')
        self.assertEqual(out[0]['collected_at'],args[0]['collected_at'])
        self.assertEqual(args[2]['signals'][0]['id'],'OLD')

    def test_fast_refresh_retains_hourly_m5_and_extended_history(self):
        args=list(self.fast_inputs())
        older={'completed_at':'2026-10-28T09:55:00Z','recent_completed_candles':[{'time':'original'}]}
        args[3]['pairs']['EUR_USD']['frames']['M5']=deepcopy(older)
        args[4]['EUR_USD']={'frames':{'M5':deepcopy(older),'W':deepcopy(older)}}
        out=exporter.select_entry_observation(*args,NOW)
        self.assertEqual(out[3]['pairs']['EUR_USD']['frames']['M5'],older)
        self.assertEqual(out[4]['EUR_USD']['frames']['M5'],older)
        self.assertEqual(out[4]['EUR_USD']['frames']['W'],older)
        self.assertEqual(out[4]['EUR_USD']['frames']['M15'],args[5]['pairs']['EUR_USD']['frames']['M15'])

    def test_stale_or_mismatched_fast_stream_cannot_override_current_signal(self):
        for change in ('stale','mismatch','future'):
            args=list(self.fast_inputs())
            if change=='mismatch':args[-1]['oanda_collected_at']='different'
            now=NOW+timedelta(minutes=21) if change=='stale' else NOW-timedelta(seconds=1) if change=='future' else NOW
            result=exporter.select_entry_observation(*args,now)
            self.assertEqual(result[2]['signals'][0]['id'],'OLD')

    def test_fast_public_observation_uses_fast_source_clock(self):
        event={'id':'X','kind':'TRIGGER_DETECTED','plan_id':'P',
               'source_at':NOW.isoformat(),'route':'B','trigger_bar_open_time':'T'}
        feed={'snapshot_at':(NOW-timedelta(minutes=10)).isoformat(),
              'hourly':{'source_at':NOW.isoformat(),'signals':[
                  {'id':'P','route':'B','trigger_bar_open_time':'T','status':'PRICE_REASSESSMENT_ALERT_READY'}]}}
        book=validation.observe_public({'events':{'X':event}},feed,NOW)
        self.assertEqual(book['events']['X']['first_observed_public_at'],NOW.isoformat())
        self.assertNotIn('verified_user_delivery_at',event)

class ValidationRepairTests(unittest.TestCase):
    def test_versioned_protocol_transition_preserves_existing_delivery_history(self):
        old={'id':'old','holdout_start_at':'2026-10-29T07:00:00Z'}
        old_digest=validation.protocol_digest(old)
        book={'locked_protocol_sha256':old_digest,'events':{'e':{'received':'unchanged'}}}
        new={'id':'new','supersedes_protocol_sha256':old_digest}
        validation.lock_protocol(book,new,NOW)
        self.assertEqual(book['events'],{'e':{'received':'unchanged'}})
        self.assertEqual(book['prior_protocol_locks'][0]['sha256'],old_digest)
        self.assertEqual(book['locked_protocol_sha256'],validation.protocol_digest(new))
        validation.lock_protocol(book,new,NOW)
        self.assertEqual(len(book['prior_protocol_locks']),1)
        tampered={**new,'threshold':1}
        validation.lock_protocol(book,tampered,NOW)
        self.assertNotEqual(book['locked_protocol_sha256'],validation.protocol_digest(tampered))

    def test_unknown_cost_cannot_become_zero_return_in_statistical_comparison(self):
        from test_forward_validation import protocol,row,NOW as LATER
        p=protocol();p['minimum_independent_scored_pair_days_per_arm']=1;p['minimum_scored_days_per_arm']=1
        rows=[row(),row('challenger_v1')]
        rows[0]['plan'].pop('pip_size')
        report=validation.evaluate(p,{'records':rows},{},'locked',LATER)
        self.assertTrue(report['readiness']['minimum_independent_samples'])
        self.assertFalse(report['readiness']['complete_cost_scenarios'])
        self.assertIsNone(report['paired_day_comparison'])
        self.assertFalse(report['performance_validated'])

if __name__=='__main__':unittest.main()

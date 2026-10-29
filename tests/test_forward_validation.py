import unittest
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from scripts import forward_validation as v
from scripts.fx_universe import deep_selection
NOW=datetime(2026,10,29,12,tzinfo=timezone.utc)

def protocol():
    return {'id':'TEST','holdout_start_at':'2026-10-29T07:00:00Z','rule_sha256':'locked',
            'minimum_independent_scored_pair_days_per_arm':100,'minimum_scored_days_per_arm':20,
            'slippage_pips_per_side':[0,.25,.5],'bootstrap_resamples':10000}

def row(policy='champion'):
    return {'policy':policy,'source_at':'2026-10-29T09:00:00Z','decision_at':'2026-10-29T09:00:05Z',
            'common_deep_history_available':True,
            'plan':{'id':'X','instrument':'EUR_USD','direction':'LONG','created_at':'2026-10-29T09:00:05Z','stop':1.099,'pip_size':.0001},
            'result':{'status':'WIN_TP1','scored':True,'entry':1.1,'exit':1.102,'entry_at':'2026-10-29T09:45:00Z','at':'2026-10-29T10:00:00Z'}}

class ForwardValidationTests(unittest.TestCase):
    def test_coverage_counts_routes_and_distinct_pairs_separately(self):
        s={'pairs':{'EUR_USD':{'frames':{tf:{'indicators':{'ema20':1}} for tf in ('M15','H1','H4','D')}}},
           'ranked_review_candidates':[{'instrument':'EUR_USD','deep_entry_scout_eligible':True}]}
        a={'candidate_routes':[{'pair':'EUR_USD','reason':'TREND'},{'pair':'EUR_USD','reason':'TREND'}]}
        c=v.coverage(s,a)
        self.assertEqual(c['route_evaluations'],2);self.assertEqual(c['distinct_evaluated_pairs'],1)
        self.assertEqual(c['route_rejections']['TREND'],2);self.assertEqual(c['distinct_pairs_by_rejection']['TREND'],1)
        self.assertEqual(c['strict_prequalified_missing_deep'],[])
        self.assertFalse(c['paper_prequalification_measured'])

    def test_all_prequalified_paper_pairs_get_history_without_approval(self):
        ranks={f'PAIR{i}':{'research_priority':i,'paper_deep_scout_eligible':True} for i in range(20)}
        self.assertEqual(len(deep_selection(ranks,max_extra=12)),20)

    def test_repository_publication_never_becomes_user_delivery(self):
        now=NOW;signals={'signals':[{'id':'X','created_at':now.isoformat()}]}
        watcher={'oanda_collected_at':now.isoformat(),'signals':[]}
        pub={'status':'PUBLISHED','snapshot_at':now.isoformat(),'checked_at':now.isoformat()}
        b=v.record_events({},signals,watcher,pub,now)
        e=b['events']['PLAN:X'];self.assertEqual(e['repository_readback_at'],now.isoformat())
        self.assertIsNone(e['verified_user_delivery_at']);self.assertEqual(e['delivery_status'],'NOT_MEASURED')

    def test_public_readback_must_match_plan_and_source(self):
        b={'events':{'e':{'kind':'CONDITIONAL_PLAN','plan_id':'X','source_at':'SOURCE'}}}
        v.observe_public(b,{'snapshot_at':'WRONG','signals':[{'id':'X'}]},NOW)
        self.assertNotIn('first_observed_public_at',b['events']['e'])
        v.observe_public(b,{'snapshot_at':'SOURCE','signals':[{'id':'OTHER'}]},NOW)
        self.assertNotIn('first_observed_public_at',b['events']['e'])
        v.observe_public(b,{'snapshot_at':'SOURCE','signals':[{'id':'X'}]},NOW)
        self.assertEqual(b['events']['e']['first_observed_public_at'],NOW.isoformat())
        self.assertNotIn('verified_user_delivery_at',b['events']['e'])

    def test_development_results_never_count_out_of_sample(self):
        r=row();r['decision_at']='2026-10-28T09:00:00Z';r['plan']['created_at']='2026-10-28T09:00:00Z'
        out=v.evaluate(protocol(),{'records':[r]}, {},'locked',NOW)
        self.assertEqual(out['excluded']['DEVELOPMENT_OR_LEGACY_TIMING'],1)
        self.assertEqual(out['arms']['champion']['independent_scored_pair_days'],0)
        self.assertFalse(out['performance_validated'])

    def test_changed_rules_invalidate_holdout(self):
        out=v.evaluate(protocol(),{'records':[row()]},{},'changed',NOW)
        self.assertEqual(out['status'],'PROTOCOL_CHANGED');self.assertFalse(out['readiness']['rules_unchanged'])
        self.assertEqual(out['arms']['champion']['independent_scored_pair_days'],0)

    def test_duplicate_fills_are_one_pair_day_not_independent_trades(self):
        r=row();later=deepcopy(r);later['plan']['id']='LATER';later['result']['entry_at']='2026-10-29T10:00:00Z'
        out=v.evaluate(protocol(),{'records':[r,later,row('challenger_v1')]},{},'locked',NOW)
        self.assertEqual(out['arms']['champion']['prospective_plans'],2)
        self.assertEqual(out['arms']['champion']['independent_scored_pair_days'],1)
        self.assertEqual(out['arms']['challenger_v1']['independent_scored_pair_days'],1)

    def test_shallow_history_is_excluded_for_both_arms(self):
        r=row('challenger_v1');r['common_deep_history_available']=False
        out=v.evaluate(protocol(),{'records':[r]},{},'locked',NOW)
        self.assertEqual(out['excluded']['INCOMPLETE_COMMON_UNIVERSE'],1)

    def test_spread_side_result_is_not_charged_spread_twice(self):
        r=row();cost=v.stress_result(r['plan'],r['result'],.25)
        self.assertAlmostEqual(cost['net_r'],1.95,5)
        self.assertFalse(cost['broker_costs_verified'])

    def test_adverse_stop_gap_increases_modelled_loss(self):
        r=row();r['result'].update(status='LOSS_STOP',exit=1.099,evidence={'exit':{'bid':{'o':1.098}}})
        cost=v.stress_result(r['plan'],r['result'],0)
        self.assertAlmostEqual(cost['net_r'],-2,5);self.assertTrue(cost['stop_gap_adjusted'])

    def test_financing_rollover_remains_unknown(self):
        r=row();r['result'].update(entry_at='2026-10-28T20:55:00Z',at='2026-10-28T21:15:00Z')
        self.assertEqual(v.stress_result(r['plan'],r['result'],0)['status'],'FINANCING_UNKNOWN')

    def test_late_or_unproven_receipt_cannot_validate_entry(self):
        r=row();event={'plan_id':'X','kind':'TRIGGER_DETECTED','detected_at':'2026-10-29T09:30:00Z',
            'verified_user_delivery_at':'2026-10-29T09:46:00Z','verification_kind':'PROVIDER_DELIVERED_RECEIPT',
            'verification_source_reference':'raw-provider-receipt'}
        b={'events':{'e':event}};self.assertFalse(v.delivered_before_entry(r,b,NOW))
        event['verified_user_delivery_at']='2026-10-29T09:31:00Z';self.assertTrue(v.delivered_before_entry(r,b,NOW))
        event.pop('verification_source_reference');self.assertFalse(v.delivered_before_entry(r,b,NOW))

    def test_sample_counts_alone_cannot_promote(self):
        p=protocol();p['minimum_independent_scored_pair_days_per_arm']=1;p['minimum_scored_days_per_arm']=1
        out=v.evaluate(p,{'records':[row(),row('challenger_v1')]},{},'locked',NOW)
        self.assertTrue(out['readiness']['minimum_independent_samples'])
        self.assertFalse(out['evidence_ready_for_independent_review']);self.assertFalse(out['automatic_promotion_allowed'])
        self.assertFalse(out['readiness']['actual_delivery_evidence']);self.assertFalse(out['readiness']['broker_cost_evidence'])

    def test_changing_acceptance_thresholds_also_invalidates_protocol(self):
        original=protocol();ledger={'locked_protocol_sha256':v.protocol_digest(original)}
        modified=deepcopy(original);modified['minimum_independent_scored_pair_days_per_arm']=1
        out=v.evaluate(modified,{'records':[row()]},ledger,'locked',NOW)
        self.assertEqual(out['status'],'PROTOCOL_CHANGED')

    def test_unverified_pip_size_does_not_invent_transaction_cost(self):
        r=row();r['plan'].pop('pip_size')
        self.assertEqual(v.stress_result(r['plan'],r['result'],.25)['status'],'SOURCE_PIP_SIZE_UNKNOWN')

    def test_non_jpy_source_pip_size_is_used(self):
        r=row();r['plan']['pip_size']=.01
        result=v.stress_result(r['plan'],r['result'],.25)
        self.assertAlmostEqual(result['net_r'],-3,5)

    def test_fixed_day_bootstrap_has_no_random_threshold_search(self):
        a=v.bootstrap_days([1,2,3],200);b=v.bootstrap_days([1,2,3],200)
        self.assertEqual(a,b);self.assertGreater(a['lower_95pct_r'],0)

    def test_post_start_without_samples_reports_collecting_not_success(self):
        out=v.evaluate(protocol(),{'records':[]},{},'locked',NOW)
        self.assertEqual(out['status'],'COLLECTING_HOLDOUT')
        self.assertEqual(out['pass_fail_decision'],'NOT_YET_VALIDATED')
        self.assertFalse(out['independent_review_completed'])

    def test_fast_detector_event_is_never_a_delivery_receipt(self):
        fast={'events':{'X|C|2026-10-29T09:15:00Z':{
            'plan_id':'X','pair':'EUR_USD','route':'C',
            'first_detected_at':'2026-10-29T09:32:00Z',
            'trigger_closed_at':'2026-10-29T09:30:00Z'}}}
        ledger=v.merge_fast_events({'events':{}},fast)
        event=next(iter(ledger['events'].values()))
        self.assertEqual(event['kind'],'TRIGGER_DETECTED')
        self.assertIsNone(event['verified_user_delivery_at'])
        self.assertFalse(v.delivered_before_entry(row(),ledger,NOW))
        self.assertEqual(ledger,v.merge_fast_events(ledger,fast))

if __name__=='__main__':unittest.main()

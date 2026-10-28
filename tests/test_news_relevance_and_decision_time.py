"""No-network regressions for blanket news vetoes and backdated research."""
import json
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from scripts import news_watch as news
from scripts import opportunity_tracker as tracker
from scripts.signal_admission import admit, news_gate
from scripts.strategy_comparison import freeze, update
from scripts.admission_diagnostics import observe
from test_signal_admission import source, ranked, news as news_fixture, NOW
from test_opportunity_tracker import snapshot, plan


def headline(title,source='Global markets aggregation',currency='GLOBAL'):
    return {'source':source,'currency_source':currency,'published_at':NOW.isoformat(),
            'title':title,'url':'https://example.org/test-source'}

class NewsRelevanceTests(unittest.TestCase):
    def test_routine_treasury_yields_do_not_veto_every_currency(self):
        hit=news.classify(headline('U.S. Treasury yields rise as traders weigh growth'))
        self.assertEqual(hit['currencies'],['USD'])
        self.assertFalse(hit['global_risk'])
        self.assertTrue(hit['global_macro_context'])
        self.assertTrue(news.requires_headline_review(hit))

    def test_named_second_jurisdiction_remains_relevant(self):
        hit=news.classify(headline('Treasury yields rise while eurozone bond yields fall'))
        self.assertIn('USD',hit['currencies']);self.assertIn('EUR',hit['currencies'])
        self.assertNotIn('JPY',hit['currencies'])

    def test_genuine_global_crisis_remains_high_impact(self):
        hit=news.classify(headline('Treasury yields jump as emergency liquidity crisis hits markets'))
        self.assertTrue(hit['global_risk']);self.assertIn('JPY',hit['currencies'])
        self.assertTrue(news.requires_headline_review(hit))

    def test_supervisory_takeover_approval_is_retained_as_context(self):
        hit=news.classify(headline('European Central Bank approves proposed takeover of local bank'))
        self.assertEqual(hit['severity'],'CONTEXT');self.assertIn('EUR',hit['currencies'])
        self.assertFalse(news.requires_headline_review(hit))

    def test_takeover_with_systemic_emergency_is_not_cleared(self):
        hit=news.classify(headline('European Central Bank emergency takeover amid liquidity crisis'))
        self.assertNotEqual(hit['severity'],'CONTEXT');self.assertTrue(news.requires_headline_review(hit))

    def test_news_context_cannot_waive_calendar_or_missing_sources(self):
        feed=news_fixture()
        feed['by_pair']['EUR_USD']['recent_relevant_headlines']=[news.classify(headline('European Central Bank approves proposed takeover of local bank'))]
        self.assertIsNone(news_gate('EUR_USD',feed,NOW))
        feed['by_pair']['EUR_USD']['near_high_impact_calendar']=[{'time':NOW.isoformat()}]
        self.assertEqual(news_gate('EUR_USD',feed,NOW),'EVENT_CALENDAR_OR_BLACKOUT')
        feed['by_pair']['EUR_USD']['near_high_impact_calendar']=[]
        feed['by_pair']['EUR_USD']['coverage']['missing_required']=['ECB press']
        self.assertEqual(news_gate('EUR_USD',feed,NOW),'PRIMARY_NEWS_SOURCE_MISSING')

    def test_legacy_unknown_severity_remains_blocking(self):
        self.assertTrue(news.requires_headline_review({'title':'Unclassified policy development'}))

class DecisionChronologyTests(unittest.TestCase):
    def summary(self):
        return {'status':'OK','collected_at':NOW.isoformat(),'ranked_review_candidates':[ranked()],'pairs':{'EUR_USD':source()}}

    def test_admission_freezes_at_decision_not_source_time(self):
        later=NOW+timedelta(minutes=2)
        saved,_,audit=admit(self.summary(),news_fixture(),{'signals':[]},evaluated_at=later.isoformat())
        self.assertEqual(saved['signals'][0]['created_at'],later.isoformat().replace('+00:00','Z'))
        self.assertEqual(saved['signals'][0]['oanda_snapshot_at'],NOW.isoformat())
        self.assertEqual(audit['evaluated_at'],later.isoformat())

    def test_study_uses_real_decision_times_and_does_not_fake_new_poll_on_rerun(self):
        later=NOW+timedelta(minutes=2)
        book=update(self.summary(),news_fixture(),fetch=lambda *args:[],decided_at=later.isoformat())
        self.assertEqual(book['observation_times'],[later.isoformat().replace('+00:00','Z')])
        saved=deepcopy(book['observation_times'])
        book=update(self.summary(),news_fixture(),book,fetch=lambda *args:[],decided_at=(later+timedelta(minutes=1)).isoformat())
        self.assertEqual(book['observation_times'],saved)
        self.assertTrue(all(r['plan']['created_at']==later.isoformat().replace('+00:00','Z') for r in book['records'] if r['plan']))

    def test_backdated_decision_cannot_generate_a_plan(self):
        earlier=(NOW-timedelta(seconds=1)).isoformat()
        saved,_,_=admit(self.summary(),news_fixture(),{'signals':[]},evaluated_at=earlier)
        self.assertEqual(saved['signals'],[])
        self.assertEqual(freeze(self.summary(),news_fixture(),decided_at=earlier),[])
        with self.assertRaises(ValueError):update(self.summary(),news_fixture(),decided_at=earlier)

    def test_watch_first_seen_is_registration_not_old_quote(self):
        registered='2026-10-27T09:16:00Z'
        with patch.object(tracker,'build_research_setups',return_value=plan()):
            book=tracker.update_ledger(snapshot(),{},registered_at=registered)
        row=next(iter(book['opportunities'].values()))
        self.assertEqual(row['first_seen_at'],'2026-10-27T09:16:00+00:00')
        self.assertEqual(row['source_quote_at'],'2026-10-27T09:13:00+00:00')
        self.assertFalse(row['entry_confirmed'])

    def test_full_gate_audit_exposes_hidden_news_veto_after_trend_failure(self):
        src=source();src['frames']['D']['indicators']['ema20']=1.098
        feed=news_fixture();feed['by_pair']['EUR_USD']['near_high_impact_calendar']=[{}]
        row=observe('EUR_USD',ranked(),src,feed,NOW,'EMA20_PULLBACK')
        self.assertEqual(row['canonical_first_rejection'],'FOUR_FRAME_TREND_CONFLICT')
        self.assertIn('trend_D',row['failed']);self.assertIn('news_gate',row['failed'])
        self.assertTrue(row['checks']['gross_reward_minimum'])
        self.assertTrue(row['not_a_signal'])

if __name__=='__main__':unittest.main()

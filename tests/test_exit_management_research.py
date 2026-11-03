"""Offline, symmetric winning and losing FX exit-management regression tests."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import exit_management_research as exit_study

T0 = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
def time(n):
    return exit_study.iso(T0 + timedelta(minutes=n))
PLAN = {'id':'future-case','instrument':'EUR_USD','direction':'LONG',
        'entry_at':time(0),'entry':1.2,'stop':1.199,'tp1':1.202,
        'pip_size':.0001,'expires_at':time(75)}
def bar(n,o,h,l,c):
    return {'time':time(n),
        'bid':{'o':o,'h':h,'l':l,'c':c},
        'ask':{'o':o+.0001,'h':h+.0001,'l':l+.0001,'c':c+.0001}}
def case():
    return {'plan':dict(PLAN),'arms':{name:exit_study.initial_arm(PLAN)
            for name in exit_study.ARM_NAMES}}
def evaluate(rows,minutes):
    return exit_study.progress_case(case(),rows,T0+timedelta(minutes=minutes))

class ExitManagementResearchTests(unittest.TestCase):
    def test_protection_after_completed_favourable_close(self):
        r=evaluate([bar(15,1.2,1.2009,1.1999,1.2008),
                    bar(30,1.20085,1.2013,1.2007,1.2011),
                    bar(45,1.2012,1.2014,1.2008,1.2010),
                    bar(60,1.20095,1.201,1.1998,1.1999)],80)
        fixed=r['arms']['FIXED_STOP_TARGET']
        protected=r['arms']['PROFIT_PROTECTION']
        self.assertEqual(fixed['status'],'OPEN')
        self.assertEqual(protected['status'],'STOP')
        self.assertTrue(protected['partial_taken'])
        self.assertTrue(protected['stop_tightened'])
        self.assertGreater(protected['gross_r'],0)

    def test_strong_winner_fixed_policy_can_outperform_protection(self):
        r=evaluate([bar(15,1.2,1.201,1.1999,1.2009),
                    bar(30,1.201,1.2019,1.2009,1.2017),
                    bar(45,1.2017,1.2021,1.2016,1.202)],65)
        fixed=r['arms']['FIXED_STOP_TARGET']
        protected=r['arms']['PROFIT_PROTECTION']
        self.assertEqual(fixed['status'],'TP1')
        self.assertEqual(protected['status'],'TP1')
        self.assertGreater(fixed['gross_r'],protected['gross_r'])
        self.assertGreater(protected['gross_r'],0)

    def test_losing_case_preserves_loss_without_fabricated_protection(self):
        r=evaluate([bar(15,1.2,1.2002,1.1989,1.1991)],31)
        for arm in exit_study.ARM_NAMES:
            self.assertEqual(r['arms'][arm]['status'],'STOP')
            self.assertEqual(r['arms'][arm]['gross_r'],-1)

    def test_expiry_is_reassessment_not_automatic_position_close(self):
        rows=[bar(15,1.2,1.2002,1.1996,1.1998),
              bar(30,1.1998,1.2001,1.1996,1.1999),
              bar(45,1.1999,1.2002,1.1997,1.1999),
              bar(60,1.1999,1.2002,1.1997,1.1998),
              bar(75,1.1998,1.2002,1.1995,1.1997)]
        r=evaluate(rows,95)
        self.assertEqual(r['arms']['PROFIT_PROTECTION']['status'],
                         'EXIT_THESIS_REASSESSMENT')
        self.assertEqual(r['arms']['PROFIT_PROTECTION']['closed_at'],time(75))
        self.assertEqual(r['arms']['FIXED_STOP_TARGET']['status'],'OPEN')
        self.assertTrue(any(a['action']=='EXIT' for a in
                            r['arms']['PROFIT_PROTECTION']['actions']))

    def test_favourable_expiry_can_hold(self):
        r=evaluate([bar(15,1.2,1.2003,1.1999,1.2002),
                    bar(30,1.2002,1.2005,1.2001,1.2004),
                    bar(45,1.2004,1.2006,1.2003,1.2005),
                    bar(60,1.2005,1.2007,1.2004,1.2006)],90)
        p=r['arms']['PROFIT_PROTECTION']
        self.assertEqual(p['status'],'OPEN')
        self.assertIn('HOLD',[a['action'] for a in p['actions']])

    def test_same_bar_target_and_stop_is_ambiguous(self):
        r=evaluate([bar(15,1.2,1.2021,1.1989,1.20)],31)
        for arm in exit_study.ARM_NAMES:
            self.assertEqual(r['arms'][arm]['status'],'AMBIGUOUS_BAR')
            self.assertIsNone(r['arms'][arm]['gross_r'])

    def test_future_candles_not_visible(self):
        r=evaluate([bar(15,1.2,1.2001,1.1998,1.20005),
                    bar(30,1.2,1.2021,1.1999,1.202)],30)
        self.assertEqual(r['arms']['FIXED_STOP_TARGET']['status'],'OPEN')

    def test_first_run_does_not_backfill_historical_fills(self):
        p={'id':'old','instrument':'EUR_USD','direction':'LONG',
            'created_at':time(0),'expires_at':time(90),
            'planned_entry':1.2,'stop':1.199,'tp1':1.202}
        record={'policy':'champion','plan':p,
                'result':{'status':'OPEN','entry_at':time(15),'entry':1.2}}
        result=exit_study.update(None,{'records':[record]}, {},
                                 T0+timedelta(minutes=40))
        self.assertEqual(result['cases'],{})
        self.assertEqual(result['metrics']['paired_scored'],0)
        self.assertFalse(result['metrics']['approved_for_live_exit_advice'])

    def test_new_paper_fill_is_registered_as_two_arms(self):
        p={'id':'new','instrument':'EUR_USD','direction':'LONG',
            'created_at':time(15),'expires_at':time(90),
            'planned_entry':1.2,'stop':1.199,'tp1':1.202}
        record={'policy':'champion','plan':p,
                'result':{'status':'OPEN','entry_at':time(30),'entry':1.2}}
        book={'schema':1,'version':exit_study.RULE_VERSION,
              'study_started_at':time(0),'cases':{}}
        output=exit_study.update(book,{'records':[record]}, {},
                                T0+timedelta(minutes=45))
        self.assertIn('new',output['cases'])
        self.assertEqual(set(output['cases']['new']['arms']),
                         set(exit_study.ARM_NAMES))
        self.assertFalse(output['metrics']['approved_for_live_exit_advice'])

if __name__ == '__main__':
    unittest.main()

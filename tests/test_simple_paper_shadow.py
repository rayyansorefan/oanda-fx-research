import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from scripts import simple_paper_shadow as sh

BASE=datetime(2026,11,5,7,0,5,tzinfo=timezone.utc)

def bar(minute,op,hi,lo,cl,spread=.0002):
    t=datetime(2026,11,5,7,tzinfo=timezone.utc)+timedelta(minutes=minute)
    def side(d):return {"o":op+d,"h":hi+d,"l":lo+d,"c":cl+d}
    return {"t":t,"mid":side(0),"bid":side(-spread/2),"ask":side(spread/2)}

def sample_plan():
    return {"id":"SHADOW-TEST","instrument":"EUR_USD","direction":"LONG",
      "setup_type":"MOMENTUM_CONTINUATION","created_at":sh.iso(BASE),
      "expires_at":"2026-11-05T08:30:05Z","trigger":1.1023,
      "indicative_entry":1.1021,"stop":1.10085,"tp1":1.10435,
      "pip_size":.0001,"atr_at_decision":.001,
      "max_spread_pips":2.5,"max_chase_atr":.35,
      "manual_response_seconds":60}

def history(*,entry_target=False,gap_stop=False):
    o=[
      bar(15,1.1021,1.1028,1.1019,1.1026),
      bar(30,1.1026,1.1029,1.1020,1.1023),
      bar(45,1.1022,1.1025,1.1018,1.1023),
      bar(60,1.1024,1.1050,1.1022,1.1046)]
    if entry_target:o[2]=bar(45,1.1022,1.1050,1.1018,1.1045)
    if gap_stop:o[3]=bar(60,1.1005,1.1008,1.1000,1.1002)
    return o

def source(at=BASE):
    m={"ema20":1.1011,"ema50":1.10,"atr14":.001}
    h={"ema20":1.101,"ema50":1.1}
    candles=[
      {"time":"2026-11-05T06:15:00Z","mid":{"o":1.101,"h":1.1014,"l":1.1006,"c":1.1012}},
      {"time":"2026-11-05T06:30:00Z","mid":{"o":1.1012,"h":1.1016,"l":1.1008,"c":1.1015}},
      {"time":"2026-11-05T06:45:00Z","mid":{"o":1.101,"h":1.1022,"l":1.1005,"c":1.102}},
    ]
    return {"collected_at":sh.iso(at),"status":"OK",
      "pairs":{"EUR_USD":{"quote":{"time":sh.iso(at-timedelta(seconds=3)),
        "tradeable":True,"bid":1.1019,"ask":1.1021,"pip_size":.0001},
        "frames":{"M15":{"indicators":m,"last_3_completed_candles":candles},
                  "H1":{"indicators":h}}}}}

class ShadowSimpleTests(unittest.TestCase):
    def test_completed_only_and_tight_liquid_pair_regime(self):
        with patch.object(sh,"news_gate",return_value=None):
            rows=sh.propose(source(),{},BASE)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["setup_type"],"MOMENTUM_CONTINUATION")
            self.assertEqual(rows[0]["entry_condition"],
                "NEXT_COMPLETED_M15_CLOSE_BEYOND_FROZEN_TRIGGER")
            self.assertFalse(rows[0]["broker_order_permitted"])
            self.assertGreater(rows[0]["estimated_net_rr_before_slippage"],1.35)
            self.assertFalse(sh.propose(source(BASE-timedelta(hours=1)),{},BASE))
            self.assertFalse(sh.propose(source(),{},BASE-timedelta(hours=1)))
            tampered=source()
            tampered["pairs"]["EUR_USD"]["frames"]["M15"]["last_3_completed_candles"][-1]["time"]="2026-11-05T07:00:00Z"
            self.assertFalse(sh.propose(tampered,{},BASE))

    def test_new_plan_has_no_eligible_future_bar_yet_not_a_permanent_gap(self):
        plan=sample_plan()
        result=sh.replay(plan,[],BASE,[])
        self.assertEqual(result["status"],"PENDING_TRIGGER")
        self.assertFalse(result["scored"])
        # After the first eligible M15 close should have arrived, missing
        # market history remains unknown rather than a fabricated no-fill.
        late=BASE+timedelta(minutes=40)
        self.assertEqual(sh.replay(plan,[],late,[])["status"],"DATA_GAP")

    def test_late_or_missing_observation_cannot_become_fill(self):
        p=sample_plan();now=datetime(2026,11,5,9,tzinfo=timezone.utc)
        early=sh.replay(p,history(),now,[])
        self.assertEqual(early["status"],"MISSED_ENTRY")
        self.assertNotIn("entry_at",early)
        late=sh.replay(p,history(),now,
            [datetime(2026,11,5,8,0,tzinfo=timezone.utc)])
        self.assertEqual(late["reason"],"LATE_OBSERVATION")
        self.assertFalse(late["scored"])

    def test_future_confirmation_then_next_bar_fill_and_target(self):
        p=sample_plan();now=datetime(2026,11,5,8,15,tzinfo=timezone.utc)
        obs=[datetime(2026,11,5,7,31,tzinfo=timezone.utc)]
        result=sh.replay(p,history(),now,obs)
        self.assertEqual(result["status"],"WIN_TP1")
        self.assertEqual(result["triggered_at"],"2026-11-05T07:30:00Z")
        self.assertEqual(result["entry_at"],"2026-11-05T07:45:00Z")
        self.assertTrue(result["scored"])
        self.assertGreater(result["net_r_bidask"],0)
        self.assertLess(result["net_r_stressed_illustrative"],
                        result["net_r_bidask"])
        self.assertFalse(result["broker_costs_verified"])

    def test_entry_bar_extreme_never_scores_a_fake_win(self):
        p=sample_plan();now=datetime(2026,11,5,8,15,tzinfo=timezone.utc)
        obs=[datetime(2026,11,5,7,31,tzinfo=timezone.utc)]
        result=sh.replay(p,history(entry_target=True),now,obs)
        self.assertEqual(result["status"],"AMBIGUOUS_ENTRY_BAR")
        self.assertFalse(result["scored"])

    def test_stop_gap_worse_than_stop_not_favourable(self):
        p=sample_plan();now=datetime(2026,11,5,8,15,tzinfo=timezone.utc)
        obs=[datetime(2026,11,5,7,31,tzinfo=timezone.utc)]
        result=sh.replay(p,history(gap_stop=True),now,obs)
        self.assertEqual(result["status"],"LOSS_STOP")
        self.assertLess(result["net_r_bidask"],-1)

    def test_missing_completed_bar_is_unverifiable(self):
        p=sample_plan();now=datetime(2026,11,5,8,15,tzinfo=timezone.utc)
        obs=[datetime(2026,11,5,7,31,tzinfo=timezone.utc)]
        result=sh.replay(p,[history()[0],history()[2],history()[3]],now,obs)
        self.assertEqual(result["status"],"DATA_GAP")
        self.assertFalse(result["scored"])

    def test_portfolio_caps_correlated_currencies_without_rewriting_fills(self):
        records={}
        for index,pair in enumerate(("EUR_USD","EUR_GBP","EUR_CHF","EUR_USD")):
            at=datetime(2026,11,5,7,45,tzinfo=timezone.utc)+timedelta(seconds=index)
            records[str(index)]={"plan":{"instrument":pair},
                "result":{"status":"OPEN","entry_at":sh.iso(at),
                          "scored":False}}
        sh.apply_portfolio_limits(records)
        self.assertEqual([r["result"]["status"] for r in records.values()],
                         ["OPEN","OPEN","PORTFOLIO_EXPOSURE_BLOCKED",
                          "PORTFOLIO_EXPOSURE_BLOCKED"])
        self.assertTrue(records["2"]["blocked_counterfactual"]["status"]=="OPEN")

    def test_preholdout_has_no_trades_and_no_broker_actions(self):
        before=BASE-timedelta(days=1)
        result=sh.update(source(before),{},now=before)
        self.assertEqual(result["status"],"HOLDOUT_NOT_STARTED")
        self.assertEqual(result["metrics"]["plans"],0)
        self.assertIsNone(result["metrics"]["net_total_r_bidask"])
        self.assertEqual(result["broker_orders_submitted"],0)
        self.assertFalse(result["automatic_promotion_allowed"])

if __name__=="__main__":
    unittest.main()

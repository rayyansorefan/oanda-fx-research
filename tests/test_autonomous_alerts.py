import datetime as dt
import unittest
from scripts.autonomous_alerts import evaluate
UTC=dt.timezone.utc
T=dt.datetime(2026,10,21,14,45,tzinfo=UTC)
def iso(d):return d.isoformat().replace("+00:00","Z")
def fixtures():
    signal={"id":"trial-1","instrument":"GBP_USD","direction":"LONG",
      "created_at":iso(T-dt.timedelta(hours=1)),"expires_at":iso(T+dt.timedelta(hours=2)),
      "planned_entry":1.3400,"stop":1.3370,"tp1":1.3460,"max_oanda_spread_pips":1.5,
      "alert_a":1.3399,"event_blackouts_utc":[]}
    s={"collected_at":iso(T-dt.timedelta(minutes=3)),"status":"PARTIAL",
       "pairs":{"GBP_USD":{"quote":{"time":iso(T-dt.timedelta(minutes=3)),
          "tradeable":True,"bid":1.33982,"ask":1.33996,"pip_size":.0001},
          "frames":{"M15":{"indicators":{"atr14":.0009}}}}}}
    news={"collected_at":iso(T-dt.timedelta(minutes=2)),"status":"PARTIAL","calendar":{"events":[]}}
    watch={"signals":[{"id":"trial-1","status":"NO_QUALIFIED_PRICE_PATTERN"}]}
    return signal,s,news,watch
class AutonomousAlertsTests(unittest.TestCase):
 def run_alert(self,change=None):
    signal,s,n,w=fixtures()
    if change:change(signal,s,n,w)
    return evaluate(s,n,{"signals":[signal]},w,now=T)
 def test_empty_approvals_never_creates_recommendation(self):
    _,s,n,w=fixtures()
    out=evaluate(s,n,{"signals":[]},w,now=T)
    self.assertEqual(out["active_alerts"],[])
    self.assertEqual(out["counts"]["REVIEW_ENTRY"],0)
 def test_approach_still_requires_entry_confirmation(self):
    x=self.run_alert()
    self.assertEqual(x["active_alerts"][0]["stage"],"APPROACHING")
    self.assertIn("M15 trigger not confirmed",x["active_alerts"][0]["reason"])
 def test_watcher_only_can_confirm_candidate(self):
    x=self.run_alert(lambda a,b,c,d:d["signals"][0].update(status="PRICE_REASSESSMENT_ALERT_SENT"))
    self.assertEqual(x["active_alerts"][0]["stage"],"REVIEW_ENTRY")
    self.assertIn("NEVER AUTO-TRADE",x["active_alerts"][0]["action"])
 def test_stale_ohlc_blocks(self):
    x=self.run_alert(lambda a,b,c,d:b.update(collected_at=iso(T-dt.timedelta(hours=2))))
    self.assertEqual(x["status"],"STALE_SOURCE")
    self.assertEqual(x["active_alerts"][0]["stage"],"BLOCK")
 def test_old_quote_blocks(self):
    x=self.run_alert(lambda a,b,c,d:b["pairs"]["GBP_USD"]["quote"].update(time=iso(T-dt.timedelta(minutes=50))))
    self.assertEqual(x["active_alerts"][0]["stage"],"BLOCK")
 def test_blackout_blocks(self):
    def mutate(a,b,c,d):
        a["event_blackouts_utc"]=[{"start":iso(T-dt.timedelta(minutes=20)),"end":iso(T+dt.timedelta(minutes=10))}]
    x=self.run_alert(mutate)
    self.assertEqual(x["active_alerts"][0]["stage"],"STAND_DOWN")
 def test_calendar_blackout(self):
    def mutate(a,b,c,d):
        c["calendar"]["events"]=[{"currency":"GBP","impact":"high","time":iso(T+dt.timedelta(minutes=20))}]
    self.assertEqual(self.run_alert(mutate)["active_alerts"][0]["stage"],"STAND_DOWN")
 def test_bad_risk_reward_never_alerts(self):
    x=self.run_alert(lambda a,b,c,d:a.update(tp1=1.3410))
    self.assertEqual(x["active_alerts"][0]["stage"],"BLOCK")
 def test_news_risk_suppresses_entry(self):
    x=self.run_alert(lambda a,b,c,d:d["signals"][0].update(status="REASSESS_NEWS_FIRST"))
    self.assertEqual(x["active_alerts"][0]["stage"],"STAND_DOWN")
 def test_expired_fails_closed(self):
    x=self.run_alert(lambda a,b,c,d:a.update(expires_at=iso(T-dt.timedelta(minutes=1))))
    self.assertEqual(x["active_alerts"][0]["stage"],"CANCEL")
 def test_deduplicate_no_stage_change(self):
    _,s,n,w=fixtures()
    old=evaluate(s,n,{"signals":[fixtures()[0]]},w,now=T)
    next=evaluate(s,n,{"signals":[fixtures()[0]]},w,previous=old,now=T)
    self.assertEqual(next["changes"],[])
if __name__=="__main__":unittest.main()

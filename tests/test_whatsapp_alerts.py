"""Opt-in WhatsApp alert delivery: time, risk, freshness and replay controls."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "scripts"))
import whatsapp_alerts as channel

NOW=datetime(2026,10,21,20,15,tzinfo=timezone.utc)
ENV={
    "WHATSAPP_OPT_IN_CONFIRMED":"yes",
    "WHATSAPP_CLOUD_TOKEN":"test-dummy-not-a-real-key",
    "WHATSAPP_PHONE_NUMBER_ID":"123456789012",
    "WHATSAPP_RECIPIENT_E164":"+447700900111",
    "WHATSAPP_TEMPLATE_NAME":"fx_research_trigger",
    "WHATSAPP_TEMPLATE_LANGUAGE":"en_GB",
    "WHATSAPP_GRAPH_VERSION":"v23.0",
}

def iso(t):
    return t.isoformat().replace("+00:00","Z")

def sample(now=NOW):
    plan={
        "id":"test_1", "instrument":"GBP_USD", "direction":"LONG",
        "created_at":iso(now-timedelta(minutes=45)),
        "expires_at":iso(now+timedelta(hours=3)),
        "planned_entry":1.35,"stop":1.349,"tp1":1.352,
        "max_oanda_spread_pips":1.5,"alert_a":1.35,"alert_b":None,
        "event_blackouts_utc":[],
    }
    bar_open=now-timedelta(minutes=30)
    row={"id":"test_1","pair":"GBP_USD","direction":"LONG",
         "status":"PRICE_REASSESSMENT_ALERT_SENT","route":"A",
         "trigger_bar_open_time":iso(bar_open),
         "news_status":"NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES"}
    snap={"status":"OK","collected_at":iso(now-timedelta(minutes=3)),
          "pairs":{"GBP_USD":{"quote":{"time":iso(now-timedelta(minutes=2)),
              "tradeable":True,"bid":1.3502,"ask":1.3503,
              "spread_pips":1.0,"pip_size":.0001}}}}
    assessment={"status":"OK","collected_at":iso(now-timedelta(minutes=2)),
                "signals":[row]}
    news={"collected_at":iso(now-timedelta(minutes=3))}
    return plan,row,snap,assessment,news

class WhatsAppAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        base=Path(self.temp.name)
        self.old={name:getattr(channel,name) for name in
                  ("ASSESSMENT","SNAPSHOT","SIGNALS","NEWS","STATE","HEALTH")}
        for name in self.old:
            setattr(channel,name,base/(name.lower()+".json"))
        self.plan,self.row,self.snapshot,self.assessment,self.news=sample()
        self.write()

    def tearDown(self):
        for name,old in self.old.items():
            setattr(channel,name,old)
        self.temp.cleanup()

    def write(self):
        for name,value in (("ASSESSMENT",self.assessment),("SNAPSHOT",self.snapshot),
                           ("SIGNALS",{"signals":[self.plan]}),("NEWS",self.news)):
            getattr(channel,name).write_text(json.dumps(value),encoding="utf-8")

    def test_disabled_without_opt_in_or_credentials(self):
        self.assertEqual(channel.configured({})[1],"OPT_IN_NOT_CONFIRMED")
        with patch.dict(channel.os.environ,{},clear=True),patch.object(channel,"send") as sender:
            self.assertEqual(channel.run(NOW)["status"],"OPT_IN_NOT_CONFIRMED")
            sender.assert_not_called()
        with patch.dict(channel.os.environ,{"WHATSAPP_OPT_IN_CONFIRMED":"yes"},clear=True):
            self.assertEqual(channel.run(NOW)["status"],"MISSING_WHATSAPP_CONFIGURATION")

    def test_uk_quiet_hours_bst_and_gmt(self):
        for text in ("2026-10-21T21:59:00Z","2026-10-26T06:59:00Z"):
            self.assertFalse(channel.awake(datetime.fromisoformat(text.replace("Z","+00:00"))))
        self.assertTrue(channel.awake(datetime(2026,10,21,6,0,tzinfo=timezone.utc)))
        self.assertTrue(channel.awake(datetime(2026,10,26,7,0,tzinfo=timezone.utc)))

    def test_complete_new_signal_sends_once_without_duplicate(self):
        with patch.dict(channel.os.environ,ENV,clear=True),patch.object(channel,"send",return_value=True) as sender:
            first=channel.run(NOW)
            second=channel.run(NOW)
        self.assertEqual(first["status"],"META_ACCEPTED")
        self.assertEqual(first["alerts_sent"],1)
        self.assertEqual(second["alerts_sent"],0)
        self.assertEqual(second["suppressed"],1)
        self.assertEqual(sender.call_count,1)
        msg=sender.call_args.args[0]
        self.assertEqual(msg["type"],"template")
        self.assertEqual(len(msg["template"]["components"][0]["parameters"]),8)
        self.assertEqual(msg["template"]["components"][0]["parameters"][0]["text"],"GBP/USD")
        # Only dry-run test numbers are present, never embedded in the actual source.
        self.assertEqual(channel.read(channel.HEALTH,{})["status"],"NO_ELIGIBLE_NEW_SIGNAL")

    def test_no_sends_for_risk_news_or_stale_conditions(self):
        scenarios=[
            ("no_watch_signal", lambda:self.assessment["signals"][0].update(status="NO_QUALIFIED_PRICE_PATTERN")),
            ("unverified_news", lambda:self.row.update(news_status="NEWS_COVERAGE_INCOMPLETE")),
            ("expired", lambda:self.plan.update(expires_at=iso(NOW-timedelta(minutes=1)))),
            ("blackout", lambda:self.plan.update(event_blackouts_utc=[{
                "start":iso(NOW-timedelta(minutes=10)),
                "end":iso(NOW+timedelta(minutes=10))}])),
            ("stale_source", lambda:self.snapshot.update(collected_at=iso(NOW-timedelta(minutes=75)))),
            ("stale_quote", lambda:self.snapshot["pairs"]["GBP_USD"]["quote"].update(
                time=iso(NOW-timedelta(minutes=25)))),
            ("failed_RR", lambda:self.plan.update(tp1=1.3505)),
            ("old_M15", lambda:self.row.update(
                trigger_bar_open_time=iso(NOW-timedelta(hours=2)))),
        ]
        for label,mutation in scenarios:
            with self.subTest(label=label):
                self.plan,self.row,self.snapshot,self.assessment,self.news=sample()
                mutation()
                self.write()
                with patch.dict(channel.os.environ,ENV,clear=True),patch.object(channel,"send") as sender:
                    result=channel.run(NOW)
                self.assertEqual(result["alerts_sent"],0)
                sender.assert_not_called()

    def test_failed_delivery_is_not_recorded_as_sent(self):
        with patch.dict(channel.os.environ,ENV,clear=True),patch.object(channel,"send",return_value=False):
            result=channel.run(NOW)
        self.assertEqual(result["status"],"META_DID_NOT_ACCEPT")
        self.assertFalse(channel.STATE.exists())

    def test_provider_payload_does_not_contain_access_token(self):
        body=channel.payload(self.plan,self.row,ENV)
        self.assertNotIn(ENV["WHATSAPP_CLOUD_TOKEN"],json.dumps(body))
        self.assertNotIn("OANDA_API_TOKEN",json.dumps(body))
        self.assertEqual(body["template"]["name"],"fx_research_trigger")

if __name__=="__main__":
    unittest.main()

"""Offline, no-network tests for WhatsApp FX research alert gating."""
import io
import json
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from scripts import whatsapp_alerts as w

NOW=datetime(2026,10,21,14,15,tzinfo=timezone.utc)
class MockTransport:
    def __init__(self):self.requests=[]
    def __call__(self,req,timeout=15):
        self.requests.append(req)
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self):return b'{"messages":[{"id":"wamid.test"}]}'
        return Response()

def sample():
    iso=lambda dt:dt.isoformat()
    signal={"id":"run123","instrument":"GBP_USD","direction":"LONG",
            "created_at":iso(NOW-timedelta(hours=1)),
            "expires_at":iso(NOW+timedelta(hours=1)),
            "planned_entry":1.3000,"stop":1.2980,"tp1":1.3040,
            "max_oanda_spread_pips":1.2,"event_blackouts_utc":[]}
    quote={"time":iso(NOW-timedelta(minutes=2)),"tradeable":True,
           "spread_pips":0.8,"pip_size":.0001,"bid":1.3002,"ask":1.30028}
    row={"id":"run123","pair":"GBP_USD","direction":"LONG",
         "status":"PRICE_REASSESSMENT_ALERT_SENT","route":"A",
         "trigger_bar_open_time":iso(NOW-timedelta(minutes=30))}
    rep={"status":"OK","collected_at":iso(NOW-timedelta(minutes=1)),
         "oanda_collected_at":iso(NOW-timedelta(minutes=2)),
         "news_collected_at":iso(NOW-timedelta(minutes=2)),
         "signals":[row]}
    return rep,{"signals":[signal]},{"pairs":{"GBP_USD":{"quote":quote}}}

class WhatsAppAlertTests(unittest.TestCase):
    def test_valid_signal_reassessment_alert(self):
        a,b,c=sample()
        events=w.build_events(a,b,c,NOW)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["kind"],"POTENTIAL_ENTRY")
        self.assertIn("Pepperstone",events[0]["details"])
    def test_stale_market_blocks_alert(self):
        a,b,c=sample()
        a["oanda_collected_at"]=(NOW-timedelta(minutes=31)).isoformat()
        self.assertEqual(w.build_events(a,b,c,NOW),[])
    def test_stale_quote_blocks_positive_alert(self):
        a,b,c=sample()
        c["pairs"]["GBP_USD"]["quote"]["time"]=(NOW-timedelta(minutes=8)).isoformat()
        self.assertEqual(w.build_events(a,b,c,NOW),[])
    def test_invalid_risk_blocks_positive_alert(self):
        a,b,c=sample()
        b["signals"][0]["tp1"]=1.3010
        self.assertEqual(w.build_events(a,b,c,NOW),[])
    def test_blackout_blocks_positive_alert(self):
        a,b,c=sample()
        b["signals"][0]["event_blackouts_utc"]=[{"start":(NOW-timedelta(minutes=5)).isoformat(),
                                                 "end":(NOW+timedelta(minutes=5)).isoformat()}]
        self.assertEqual(w.build_events(a,b,c,NOW),[])
    def test_no_new_signal_no_alert(self):
        a,b,c=sample()
        a["signals"][0]["status"]="NO_QUALIFIED_PRICE_PATTERN"
        self.assertEqual(w.build_events(a,b,c,NOW),[])
    def test_quiet_hours_uk_dst(self):
        self.assertTrue(w.quiet_hours(datetime(2026,10,21,22,1,tzinfo=timezone.utc)))
        self.assertFalse(w.quiet_hours(datetime(2026,10,21,21,1,tzinfo=timezone.utc)))
        self.assertTrue(w.quiet_hours(datetime(2026,12,21,0,1,tzinfo=timezone.utc)))
        self.assertFalse(w.quiet_hours(datetime(2026,12,21,7,1,tzinfo=timezone.utc)))
    def test_api_payload_is_template_only_and_secret_protected(self):
        event={"summary":"GBP/USD LONG review","details":"Confirmation required"}
        cfg={"FX_WA_RECIPIENT":"447900000000","FX_WA_PHONE_NUMBER_ID":"1234567890",
             "FX_WA_ACCESS_TOKEN":"TEST_NONPRODUCTION","FX_WA_TEMPLATE_NAME":"fx_research_alert",
             "FX_WA_LANGUAGE":"en_GB"}
        transport=MockTransport()
        self.assertTrue(w.send_message(event,cfg,transport))
        req=transport.requests[0]
        body=json.loads(req.data)
        self.assertEqual(body["type"],"template")
        self.assertEqual(body["template"]["components"][0]["parameters"][0]["text"],event["summary"])
        self.assertNotIn("TEST_NONPRODUCTION",req.data.decode())
    def test_no_personal_numbers_embedded_in_status(self):
        a,b,c=sample()
        self.assertNotIn("recipient",w.build_events(a,b,c,NOW)[0]["summary"].lower())
    def test_alert_keys_repeat_deterministically(self):
        a,b,c=sample()
        self.assertEqual(w.build_events(a,b,c,NOW)[0]["key"],w.build_events(a,b,c,NOW)[0]["key"])

    def test_missing_sender_credentials_are_visible_without_trade_signals(self):
        from unittest.mock import patch
        with patch.object(w, "quiet_hours", return_value=False), \
             patch.object(w, "read", return_value={}), \
             patch.object(w, "build_events", return_value=[]), \
             patch.object(w, "emit_status") as report, \
             patch.dict("os.environ", {}, clear=True):
            self.assertEqual(w.main(), 0)
            self.assertEqual(report.call_args.args[0], "NOT_CONFIGURED")

if __name__=="__main__":
    unittest.main()

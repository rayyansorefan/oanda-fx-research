"""No-network tests for a true, optional WhatsApp test-only message."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from scripts import whatsapp_live_test as live

NOW=datetime(2026,10,21,20,40,tzinfo=timezone.utc)
CONFIG={
    "FX_WA_ACCESS_TOKEN":"non-production-token",
    "FX_WA_PHONE_NUMBER_ID":"11223344",
    "FX_WA_RECIPIENT":"447700900123",
    "FX_WA_TEMPLATE_NAME":"fx_research_alert",
}
class WhatsAppLiveTestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.original=live.RESULT
        live.RESULT=Path(self.temp.name)/"result.json"
    def tearDown(self):
        live.RESULT=self.original
        self.temp.cleanup()
    def test_missing_credentials_never_sends(self):
        with patch.dict(live.os.environ,{},clear=True),patch.object(live.wa,"quiet_hours",return_value=False),patch.object(live.wa,"send_message") as send:
            self.assertEqual(live.main(),2)
            send.assert_not_called()
        content=json.loads(live.RESULT.read_text())
        self.assertEqual(content["status"],"NOT_CONFIGURED")
        self.assertFalse(content["is_trading_signal"])
    def test_authorised_connection_test_calls_real_transport_once(self):
        with patch.dict(live.os.environ,CONFIG,clear=True),patch.object(live.wa,"quiet_hours",return_value=False),patch.object(live.wa,"send_message",return_value=True) as send:
            self.assertEqual(live.main(),0)
            send.assert_called_once()
        event=send.call_args.args[0]
        self.assertIn("TEST ONLY",event["summary"])
        self.assertIn("NO trading signal",event["details"])
        result=json.loads(live.RESULT.read_text())
        self.assertEqual(result["status"],"META_ACCEPTED")
        self.assertFalse(result["handset_delivery_verified"])
        self.assertNotIn(CONFIG["FX_WA_RECIPIENT"],json.dumps(result))
        self.assertNotIn(CONFIG["FX_WA_ACCESS_TOKEN"],json.dumps(result))
    def test_quiet_hours_prevent_send(self):
        with patch.dict(live.os.environ,CONFIG,clear=True),patch.object(live.wa,"quiet_hours",return_value=True),patch.object(live.wa,"send_message") as send:
            self.assertEqual(live.main(),2)
            send.assert_not_called()
        self.assertEqual(json.loads(live.RESULT.read_text())["status"],"QUIET_HOURS")

if __name__=="__main__":unittest.main()

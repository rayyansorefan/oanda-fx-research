import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import oanda_data as data
import signal_watch as watch

class DataTests(unittest.TestCase):
    def test_ema_flat(self):
        self.assertAlmostEqual(data.ema([10.0]*250, 200), 10.0)
    def test_rsi_flat(self):
        self.assertAlmostEqual(data.rsi([10.0]*50), 50.0)
    def test_rsi_trend(self):
        self.assertGreater(data.rsi([10+i for i in range(50)]), 99.0)
    def test_short_history(self):
        with self.assertRaises(ValueError):
            data.describe([])
    def test_risk_guard(self):
        s={"instrument":"EUR_USD","direction":"LONG","planned_entry":1.101,
           "stop":1.098,"tp1":1.107}
        self.assertTrue(watch.valid_risk(s,1.2))
        s["tp1"]=1.102
        self.assertFalse(watch.valid_risk(s,1.2))


    def test_hourly_rechecks_prior_completed_m15_bars(self):
        from datetime import datetime, timezone, timedelta
        def bar(start, opening, high, low, close):
            return {"time": start.isoformat().replace("+00:00", "Z"),
                    "mid": {"o": opening, "h": high, "l": low, "c": close}}
        n=datetime(2026, 10, 20, 12, 7, tzinfo=timezone.utc)
        sig={"instrument":"EUR_USD", "direction":"LONG",
             "created_at":"2026-10-20T11:00:00Z",
             "alert_b":1.1000, "planned_entry":1.101,
             "stop":1.098, "tp1":1.107,
             "max_oanda_spread_pips":2}
        bars=[
            bar(n-timedelta(minutes=67),1.101,1.1015,1.1005,1.101),
            bar(n-timedelta(minutes=52),1.1001,1.1008,1.0998,1.1005),
            bar(n-timedelta(minutes=37),1.101,1.1015,1.1008,1.1012),
            bar(n-timedelta(minutes=22),1.101,1.1014,1.1009,1.1011)]
        quotes={"time":"2026-10-20T12:06:40Z", "bid":1.101,
                "ask":1.10112, "spread_pips":1.2, "tradeable":True}
        tracked={"notified":[]}
        result=watch.signal_match(sig,{"quote":quotes,"frames":{
            "M15":{"recent_completed_candles":bars}}},tracked,n)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "B")
        self.assertEqual(result[1], bars[1]["time"])
        sig["created_at"]="2026-10-20T12:01:00Z"
        self.assertIsNone(watch.signal_match(sig,{"quote":quotes,"frames":{
            "M15":{"recent_completed_candles":bars}}},{"notified":[]},n))

if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch
from scripts import simple_shadow_gate_diagnostics as diagnostics


class SimpleShadowGateTelemetryTests(unittest.TestCase):
    def summary(self, stamp="2026-11-04T14:34:00Z"):
        return {"status":"OK", "collected_at":stamp, "pairs":{}}

    def test_absence_is_reported_not_inferred_fill(self):
        report=diagnostics.collect(self.summary(), {}, "2026-11-04T14:34:10Z", state={})
        self.assertEqual(report["total_evaluated_snapshots"],1)
        self.assertEqual(report["total_pair_gate_counts"]["PAIR_SOURCE_MISSING"],10)
        self.assertFalse(report["performance_validated"])
        self.assertTrue(report["no_broker_orders"])
        self.assertNotIn("records",report)

    def test_before_start_does_not_reclassify_historical_observation(self):
        report=diagnostics.collect(self.summary("2026-11-04T14:20:00Z"),{},
                                   "2026-11-04T14:20:10Z",state={"observations":[]})
        self.assertEqual(report["observations"],[])

    def test_same_snapshot_is_not_counted_twice(self):
        now="2026-11-04T14:34:10Z"
        first=diagnostics.collect(self.summary(),{},now,state={})
        second=diagnostics.collect(self.summary(),{},now,state=first)
        self.assertEqual(second["total_evaluated_snapshots"],1)
        self.assertEqual(len(second["observations"]),1)

    def test_unusable_price_is_explicit_before_indicator_checks(self):
        summary=self.summary()
        summary["pairs"]["EUR_USD"]={"quote":{"tradeable":True,
            "time":"2026-11-04T14:34:00Z","bid":1.1,"ask":1.0999}}
        result=diagnostics.first_blocker("EUR_USD",summary,{},
                                        diagnostics.sh.when("2026-11-04T14:34:10Z"))
        self.assertEqual(result,"BID_ASK_INVALID")

    def test_technical_alignment_precedes_news(self):
        now=diagnostics.sh.when("2026-11-04T14:34:10Z")
        frames={"M15":{"completed_at":"2026-11-04T14:15:00Z",
                       "indicators":{"ema20":1.10,"ema50":1.09,"atr14":0.001}},
                "H1":{"completed_at":"2026-11-04T13:00:00Z",
                      "indicators":{"ema20":1.08,"ema50":1.10}}}
        summary=self.summary()
        summary["pairs"]["EUR_USD"]={"quote":{
            "tradeable":True,"time":"2026-11-04T14:34:00Z",
            "bid":1.1000,"ask":1.1001,"pip_size":0.0001},"frames":frames}
        with patch.object(diagnostics.sh,"news_gate",return_value=None):
            result=diagnostics.first_blocker("EUR_USD",summary,{},now)
        self.assertEqual(result,"M15_H1_EMA_DIRECTION_CONFLICT")


if __name__=="__main__":
    unittest.main()

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

if __name__ == "__main__":
    unittest.main()

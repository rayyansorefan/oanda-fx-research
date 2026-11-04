import unittest
from scripts.paper_portfolio_audit import report

START="2026-11-05T07:00:00Z"
ENTRY="2026-11-05T07:30:00Z"
EXIT="2026-11-05T08:30:00Z"

def rec(name, pair="EUR_USD", *, status="WIN_TP1", entry=ENTRY, exit_at=EXIT):
    plan={"id":name,"instrument":pair,"created_at":"2026-11-05T07:00:00Z",
          "planned_entry":1.1000,"stop":1.0990,"tp1":1.1020,"pip_size":0.0001}
    result={"status":status,"entry_at":entry,"entry":1.1000,
            "at":exit_at,"net_r":1.5,"scored":True}
    if status=="OPEN":
        result.pop("at");result.pop("net_r");result["scored"]=False
    return {"policy":"challenger_v1","plan":plan,"result":result}

def run(rows, observed="2026-11-05T10:00:00Z"):
    return report({"records":rows},{"id":"TEST","holdout_start_at":START},observed)

class PaperPortfolioTests(unittest.TestCase):
    def test_no_fill_no_success(self):
        row=rec("X")
        row["result"]={"status":"TRIGGERED_UNFILLED","triggered_at":ENTRY,"scored":False}
        out=run([row])
        arm=out["arms"]["challenger_v1"]
        self.assertEqual(arm["independently_scored_exits"],0)
        self.assertEqual(arm["eligible_paper_fills_after_portfolio_limits"],0)
        self.assertIsNone(arm["total_oanda_bidask_r"])
        self.assertFalse(out["performance_validated"])

    def test_cost_stress_preserves_bidask_and_subtracts_only_extra_cost(self):
        out=run([rec("X")])
        arm=out["arms"]["challenger_v1"]
        self.assertAlmostEqual(arm["total_oanda_bidask_r"],1.5)
        self.assertAlmostEqual(arm["cost_stress_total_r"]["0"],1.47)
        self.assertAlmostEqual(arm["cost_stress_total_r"]["0.5"],1.37)
        self.assertFalse(out["broker_costs_verified"])

    def test_correlated_currency_cap_and_same_pair(self):
        rows=[rec("A","EUR_USD"),rec("B","EUR_GBP"),rec("C","EUR_CHF"),
              rec("D","EUR_USD")]
        out=run(rows)
        arm=out["arms"]["challenger_v1"]
        self.assertEqual(arm["eligible_paper_fills_after_portfolio_limits"],2)
        self.assertEqual(arm["blocked_overlapping_entries"],2)
        self.assertEqual(arm["overlap_reasons"]["CURRENCY_CONCENTRATION"],1)
        self.assertEqual(arm["overlap_reasons"]["DUPLICATE_OPEN_PAIR"],1)

    def test_open_paper_is_exposure_not_scored_win(self):
        out=run([rec("X",status="OPEN")])
        arm=out["arms"]["challenger_v1"]
        self.assertEqual(arm["open_paper_positions"],1)
        self.assertEqual(arm["independently_scored_exits"],0)
        self.assertEqual(arm["open_exposure_pairs"],["EUR_USD"])

    def test_old_development_not_relabelled_forward(self):
        row=rec("OLD")
        row["plan"]["created_at"]="2026-11-04T07:00:00Z"
        out=run([row])
        arm=out["arms"]["challenger_v1"]
        self.assertEqual(arm["excluded"]["HISTORICAL_NOT_FORWARD"],1)
        self.assertEqual(arm["independently_scored_exits"],0)

if __name__=="__main__":
    unittest.main()

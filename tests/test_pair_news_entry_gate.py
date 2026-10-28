import unittest
from scripts.entry_readiness import entry_diagnostics

class PairNewsAdmissionTests(unittest.TestCase):
    def source(self):
        def ind():
            return {"ema20":1.102,"ema50":1.100,"adx14":30,
                    "atr14":.0008,"plus_di14":30,"minus_di14":10,
                    "resistance20":1.108,"support20":1.098}
        return {"collected_at":"2026-10-28T15:02:00Z",
                "ranked_review_candidates":[{"instrument":"EUR_USD",
                    "potential_direction_not_approved":"LONG",
                    "research_priority":75}],
                "pairs":{"EUR_USD":{
                    "quote":{"time":"2026-10-28T15:01:00Z",
                        "bid":1.101,"ask":1.1011,"pip_size":.0001,
                        "spread_pips":1,"tradeable":True},
                    "frames":{"M15":{"indicators":ind(),
                        "last_3_completed_candles":[
                            {"time":"2026-10-28T14:30:00Z",
                             "mid":{"o":1.100,"h":1.101,"l":1.099,"c":1.1005}},
                            {"time":"2026-10-28T14:45:00Z",
                             "mid":{"o":1.1005,"h":1.1015,"l":1.1002,"c":1.101}}]},
                        "H1":{"indicators":ind()},
                        "H4":{"indicators":ind()},
                        "D":{"indicators":ind()}}}}}
    def test_optional_gaps_are_not_all_pair_clearance(self):
        news={"status":"PARTIAL","by_pair":{"EUR_USD":{
            "calendar_coverage":"OK","near_high_impact_calendar":[],
            "recent_relevant_headlines":[],
            "coverage":{"status":"LIMITED","missing_required":[]}}}}
        row=entry_diagnostics(self.source(), news)[0]
        codes={x["code"] for x in row["blockers"]}
        self.assertIn("NEWS_OPTIONAL_GAPS",codes)
        self.assertNotIn("NEWS_PARTIAL",codes)
        self.assertFalse(row["approved_entry"])
    def test_critical_gates_remain_visible(self):
        news={"status":"OK","by_pair":{"EUR_USD":{
            "calendar_coverage":"OK",
            "near_high_impact_calendar":[{"time":"2026-10-28T15:15:00Z"}],
            "recent_relevant_headlines":[{"title":"Policy update"}],
            "coverage":{"status":"LIMITED","missing_required":["Required source"]}}}}
        row=entry_diagnostics(self.source(), news)[0]
        codes={x["code"] for x in row["blockers"]}
        self.assertIn("NEWS_EVENT_BLACKOUT",codes)
        self.assertIn("NEWS_MATERIAL_REVIEW",codes)
        self.assertIn("NEWS_REQUIRED_SOURCE_MISSING",codes)
        self.assertFalse(row["approved_entry"])

if __name__=="__main__":
    unittest.main()

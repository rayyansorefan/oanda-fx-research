"""Distinct hourly research snapshots must not overwrite or masquerade as trades."""
import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_trade_audit import ranked_priority_watches, update_book, public_view

def fixture(at):
    pair = "EUR_USD"
    return (
        {"collected_at": at, "ranked_review_candidates": [
            {"instrument": pair, "potential_direction_not_approved": "SHORT",
             "research_priority": 76.0}]},
        {"collected_at": at, "pairs": {pair: {"quote": {
            "time": at, "bid": 1.1000, "ask": 1.1002,
            "tradeable": True}}}},
    )

class HourlyCohortTests(unittest.TestCase):
    def test_each_new_snapshot_creates_distinct_immutable_watch(self):
        book = {"candidates": {}, "warnings": []}
        ids = []
        for at in ("2026-10-26T15:15:00Z", "2026-10-26T16:15:00Z",
                   "2026-10-26T17:15:00Z"):
            summary, market = fixture(at)
            now = dt.datetime.fromisoformat(at.replace("Z", "+00:00")) + dt.timedelta(minutes=2)
            watches = ranked_priority_watches(summary, market, book, now)
            self.assertEqual(len(watches), 1)
            ids.append(watches[0]["id"])
            update_book(book, {"candidates": watches}, {"signals": []}, now, market=market)
            self.assertEqual(ranked_priority_watches(summary, market, book, now), [])
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(len(book["candidates"]), 3)
        self.assertTrue(all(not x["result"].get("scored") for x in book["candidates"].values()))
        self.assertEqual(public_view(book)["totals"]["wins"], 0)

    def test_top_three_retained_and_lower_ranked_novelty_prioritised(self):
        at = "2026-10-26T19:32:00Z"
        now = dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
        ranked = [{"instrument": f"EUR_{c}", "potential_direction_not_approved": "SHORT",
                   "research_priority": 80-i} for i,c in enumerate(
                       ["USD","GBP","JPY","CAD","CHF","AUD","NZD","SEK"])]
        quotes = {r["instrument"]:{"quote":{"time":at,"bid":1.1,"ask":1.1001,
                  "tradeable":True}} for r in ranked}
        book={"candidates":{}}
        for r in ranked[3:6]:
            k="PRIORITY-20261026T183200Z-"+r["instrument"]+"-SHORT"
            book["candidates"][k]={"plan":{"id":k,"instrument":r["instrument"],
              "direction":"SHORT","created_at":"2026-10-26T18:32:00Z"}}
        found=ranked_priority_watches({"collected_at":at,
          "ranked_review_candidates":ranked},{"collected_at":at,"pairs":quotes},book,now,limit=6)
        self.assertEqual([x["instrument"] for x in found],
                         ["EUR_USD","EUR_GBP","EUR_JPY","EUR_NZD","EUR_SEK","EUR_CAD"])
        self.assertEqual(len(found),6)

    def test_same_hour_distinct_main_and_hourly_snapshots_retained(self):
        book = {"candidates": {}}
        ids = []
        for at in ("2026-10-26T14:00:00Z", "2026-10-26T14:15:00Z"):
            summary, market = fixture(at)
            now = dt.datetime.fromisoformat(at.replace("Z", "+00:00")) + dt.timedelta(minutes=1)
            ids.extend(x["id"] for x in ranked_priority_watches(summary, market, book, now))
        self.assertEqual(len(set(ids)), 2)

if __name__ == "__main__":
    unittest.main()

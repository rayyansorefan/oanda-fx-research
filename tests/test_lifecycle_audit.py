import copy
import unittest
from scripts.lifecycle_audit import source_lifecycle, state_for


SOURCE = "2026-11-04T10:40:00Z"
START = "2026-11-04T10:30:00Z"


def plan(id="P-1", *, created="2026-11-04T10:31:00Z",
         expiry="2026-11-04T12:00:00Z"):
    return {
        "id": id, "instrument": "EUR_USD", "created_at": created,
        "expires_at": expiry, "direction": "LONG", "entry_route": "MOMENTUM_CONTINUATION",
        "planned_entry": 1.1005, "stop": 1.0990, "tp1": 1.1035,
        "trigger_timeframe": "M5", "alert_c": 1.1000
    }


def evaluate(*, records=None, signals=None, archived=None, previous=None, source=SOURCE,
             assessed="2026-11-04T10:45:00Z", events=None, watches=None):
    return source_lifecycle(
        {"collected_at": source, "ranked_review_candidates": [{"instrument": "EUR_USD"}]},
        {"records": records or []},
        {"signals": signals or []}, {"signals": archived or []},
        {"events": events or {}}, {"opportunities": watches or {}},
        previous, assessed_at=assessed, holdout_start_at=START,
    )


class LifecycleAuditTests(unittest.TestCase):
    def test_canonical_scored_paper_outcome_overrides_entry_expiry(self):
        base=plan(expiry="2026-11-04T10:35:00Z")
        result={"status":"LOSS_STOP","scored":True,"net_r":-1,
                "entry_at":"2026-11-04T10:34:00Z","at":"2026-11-04T10:39:00Z"}
        out=source_lifecycle({"collected_at":SOURCE},{},{"signals":[base]}, {}, {}, {},
            assessed_at="2026-11-04T10:45:00Z",
            paper_book={"candidates":{"p":{"plan":base,"result":result}}})
        rec=next(iter(out["plans"].values()))
        self.assertEqual(rec["current_state"],"CLOSED")
        self.assertEqual(rec["net_r"],-1)
        unknown=evaluate(signals=[base])
        self.assertEqual(next(iter(unknown["plans"].values()))["current_state"],"UNVERIFIABLE")

    def test_actual_paper_frozen_schema_omits_descriptive_entry_route(self):
        from scripts.paper_trade_audit import frozen
        original=plan(expiry="2026-11-04T10:35:00Z")
        paper=frozen(original)
        self.assertNotIn("entry_route", paper)
        result={"status":"LOSS_STOP","scored":True,"net_r":-1,
                "entry_at":"2026-11-04T10:34:00Z","at":"2026-11-04T10:39:00Z"}
        out=source_lifecycle({"collected_at":SOURCE},{},{"signals":[original]}, {}, {}, {},
            assessed_at="2026-11-04T10:45:00Z",
            paper_book={"candidates":{"legacy":{"plan":paper,"result":result}}})
        rec=next(iter(out["plans"].values()))
        self.assertEqual(rec["current_state"],"CLOSED")
        self.assertTrue(rec["scored"])
        self.assertFalse(out["issues"])

    def test_same_id_different_stop_or_expiry_cannot_attach_paper_loss(self):
        original=plan(expiry="2026-11-04T10:35:00Z")
        conflicting=copy.deepcopy(original)
        conflicting["stop"]=1.0960
        result={"status":"LOSS_STOP","scored":True,"net_r":-1,
                "entry_at":"2026-11-04T10:34:00Z","at":"2026-11-04T10:39:00Z"}
        out=source_lifecycle({"collected_at":SOURCE},{},{"signals":[original]}, {}, {}, {},
            assessed_at="2026-11-04T10:45:00Z",
            paper_book={"candidates":{"mismatch":{"plan":conflicting,"result":result}}})
        rec=next(iter(out["plans"].values()))
        self.assertEqual(rec["current_state"],"UNVERIFIABLE")
        self.assertFalse(rec["scored"])
        self.assertNotIn("net_r",rec)
        self.assertIn("PAPER_ECONOMICS_MISMATCH:"+original["id"],out["issues"])

    def test_equivalent_utc_spelling_attaches_matching_result(self):
        original=plan(expiry="2026-11-04T10:35:00Z")
        paper=copy.deepcopy(original)
        paper["created_at"]="2026-11-04T10:31:00+00:00"
        paper["expires_at"]="2026-11-04T10:35:00+00:00"
        result={"status":"LOSS_STOP","scored":True,"net_r":-1,
                "entry_at":"2026-11-04T10:34:00Z","at":"2026-11-04T10:39:00Z"}
        out=source_lifecycle({"collected_at":SOURCE},{},{"signals":[original]}, {}, {}, {},
            assessed_at="2026-11-04T10:45:00Z",
            paper_book={"candidates":{"p":{"plan":paper,"result":result}}})
        rec=next(iter(out["plans"].values()))
        self.assertEqual(rec["current_state"],"CLOSED")
        self.assertFalse(out["issues"])

    def test_conflicting_matching_results_are_not_silently_selected(self):
        original=plan(expiry="2026-11-04T10:35:00Z")
        def result(status):
            return {"status":status,"scored":True,"net_r":-1 if status=="LOSS_STOP" else 2,
                    "entry_at":"2026-11-04T10:34:00Z","at":"2026-11-04T10:39:00Z"}
        out=source_lifecycle({"collected_at":SOURCE},{},{"signals":[original]}, {}, {}, {},
            assessed_at="2026-11-04T10:45:00Z",
            paper_book={"candidates":{
                "a":{"plan":original,"result":result("LOSS_STOP")},
                "b":{"plan":original,"result":result("WIN_TP1")}}})
        rec=next(iter(out["plans"].values()))
        self.assertEqual(rec["current_state"],"UNVERIFIABLE")
        self.assertFalse(rec["scored"])
        self.assertIn("CONTRADICTORY_PAPER_OUTCOMES:"+original["id"],out["issues"])

    def test_unique_plans_not_repeated_snapshots_or_broker_trades(self):
        base=plan()
        rows=[{"policy": "challenger_v1", "plan": copy.deepcopy(base),
               "result": {"status": "PENDING_TRIGGER"}},
              {"policy": "challenger_v1", "plan": copy.deepcopy(base),
               "result": {"status": "PENDING_TRIGGER"}}]
        out=evaluate(records=rows,signals=[base],watches={"w":{"id":"w","instrument":"EUR_USD"}})
        self.assertEqual(out["summary"]["paper_strategy_comparison"]["challenger_v1"]["plans"],1)
        self.assertEqual(out["summary"]["conditional_frozen_plans"],1)
        self.assertEqual(out["summary"]["distinct_unadmitted_watch_observations"],1)
        self.assertEqual(out["summary"]["manual_broker_trades_verified"],0)
        self.assertEqual(out["summary"]["paper_strategy_comparison"]["challenger_v1"]["scored"],0)
        self.assertEqual(out,evaluate(records=rows,signals=[base],watches={"w":{"id":"w","instrument":"EUR_USD"}},previous=out))

    def test_frozen_paper_plan_without_trigger_is_pending_not_watch(self):
        out=evaluate(records=[{"policy":"champion", "plan":plan(), "result":{}}])
        row=next(iter(out["plans"].values()))
        self.assertEqual(row["current_state"],"QUALIFIED_PENDING_TRIGGER")
        self.assertEqual(out["summary"]["distinct_unadmitted_watch_observations"],0)

    def test_trigger_does_not_automatically_mean_fill(self):
        base=plan()
        row={"policy":"challenger_v1","plan":base,"result":{"status":"TRIGGERED_UNFILLED",
             "triggered_at":"2026-11-04T10:35:00Z"}}
        out=evaluate(records=[row])
        rec=next(x for x in out["plans"].values() if x["source_kind"]=="paper_comparison")
        self.assertEqual(rec["current_state"],"TRIGGERED")
        self.assertTrue(rec["trigger_seen"])
        self.assertFalse(rec["scored"])
        self.assertEqual(out["summary"]["paper_strategy_comparison"]["challenger_v1"]["scored"],0)

    def test_expiry_is_not_a_paper_close(self):
        base=plan(expiry="2026-11-04T10:35:00Z")
        openrow={"policy":"champion","plan":base,"result":{
            "status":"OPEN","entry_at":"2026-11-04T10:34:00Z","entry":1.1005,
            "triggered_at":"2026-11-04T10:33:00Z"}}
        out=evaluate(records=[openrow], assessed="2026-11-04T10:50:00Z")
        self.assertEqual(next(iter(out["plans"].values()))["current_state"],"PAPER_OPEN")
        self.assertEqual(state_for({"status":"TRIGGERED_UNFILLED"},base["expires_at"],
                                   __import__("datetime").datetime.fromisoformat("2026-11-04T10:50:00+00:00")),
                         "EXPIRED_UNFILLED")

    def test_outcome_requires_an_explicitly_scored_exit(self):
        base=plan()
        filled={"policy":"champion","plan":base,"result":{
            "status":"LOSS_STOP","entry_at":"2026-11-04T10:34:00Z",
            "at":"2026-11-04T10:39:00Z","entry":1.1005,"exit":1.0990,
            "scored":True,"net_r":-1}}
        out=evaluate(records=[filled])
        row=next(iter(out["plans"].values()))
        self.assertEqual(row["current_state"],"CLOSED")
        self.assertEqual(row["net_r"],-1)
        self.assertTrue(row["scored"])
        self.assertEqual(len(out["events"]),1)
        later=evaluate(records=[filled],previous=out)
        self.assertEqual(len(later["events"]),1)

    def test_conflicting_original_plan_does_not_mutate(self):
        original=plan()
        out=evaluate(signals=[original])
        changed=plan()
        changed["tp1"]=1.1099
        later=evaluate(signals=[changed],previous=out)
        self.assertTrue(any(s.startswith("IMMUTABLE_CONFLICT") for s in later["issues"]))
        key=next(iter(out["plans"]))
        self.assertEqual(out["plans"][key]["original"],later["plans"][key]["original"])

    def test_out_of_order_source_does_not_regress(self):
        out=evaluate(signals=[plan()])
        old=evaluate(signals=[plan()],previous=out,
                     source="2026-11-04T10:32:00Z")
        self.assertEqual(out,old)

    def test_synthetic_unknown_is_unverifiable(self):
        out=evaluate(records=[{"policy":"champion","plan":plan(),"result":{
            "status":"AMBIGUOUS_ENTRY_BAR","scored":False}}])
        self.assertEqual(next(iter(out["plans"].values()))["current_state"],"UNVERIFIABLE")
        self.assertEqual(out["summary"]["paper_strategy_comparison"]["champion"]["scored"],0)

    def test_fast_detection_is_not_delivery(self):
        base=plan()
        events={"trigger":{"plan_id":base["id"],"plan_created_at":base["created_at"],
                           "first_detected_at":"2026-11-04T10:35:12Z"}}
        out=evaluate(signals=[base],events=events)
        self.assertEqual(next(iter(out["plans"].values()))["current_state"],"TRIGGERED")
        self.assertEqual(out["summary"]["verified_user_delivery_receipts"],0)
        self.assertFalse(any("broker" in str(k).lower() for k in out["events"].keys()))


if __name__ == "__main__":
    unittest.main()


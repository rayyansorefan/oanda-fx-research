import unittest
from copy import deepcopy
from scripts.paper_evidence_guard import reconcile


class EvidenceGuardTests(unittest.TestCase):
    def setUp(self):
        self.old = {"status":"OPEN", "entry":1.1, "entry_at":"2026-11-05T08:00:00Z",
                    "scored":False, "source_evidence":"original.json"}

    def test_outage_and_gap_retain_original_fill_and_source(self):
        for status in ("SOURCE_UNAVAILABLE", "DATA_GAP", "UNTRIGGERED"):
            record={"result":deepcopy(self.old)}
            got=reconcile(record,{"status":status,"scored":False},"now")
            self.assertEqual(got["entry"],1.1)
            self.assertEqual(got["source_evidence"],"original.json")
            self.assertEqual(got["status"],"OPEN")
            self.assertEqual(got["data_status"],"REPLAY_UNVERIFIABLE")
            self.assertEqual(record["replay_conflicts"][0]["candidate"]["status"],status)

    def test_repriced_fill_cannot_overwrite_recorded_fill(self):
        record={"result":self.old}
        got=reconcile(record,{**self.old,"entry":1.2,"status":"WIN_TP1","scored":True},"now")
        self.assertEqual(got["entry"],1.1)
        self.assertFalse(got["scored"])

    def test_compatible_close_preserves_entry_evidence(self):
        record={"result":self.old}
        got=reconcile(record,{**self.old,"status":"LOSS_STOP","scored":True,"net_r":-1},"now")
        self.assertEqual(got["status"],"LOSS_STOP")
        self.assertEqual(got["original_entry_evidence"]["source_evidence"],"original.json")

    def test_scored_terminal_is_immutable(self):
        record={"result":{**self.old,"status":"LOSS_STOP","scored":True,"net_r":-1}}
        self.assertEqual(reconcile(record,{"status":"OPEN"},"now"),record["result"])

class WorkflowIntegrationTests(unittest.TestCase):
    def test_comparison_fetch_outage_does_not_erase_open_fill(self):
        from scripts import strategy_comparison as c
        from datetime import datetime, timezone
        now=datetime(2026,11,5,9,tzinfo=timezone.utc)
        row={"policy":"champion","pair":"EUR_USD","source_at":now.isoformat(),
             "plan":{"id":"T","created_at":"2026-11-05T07:00:00Z"},
             "result":{"status":"OPEN","entry":1.1,"entry_at":"2026-11-05T08:00:00Z",
                       "source_evidence":"immutable.json","scored":False}}
        book={"records":[row],"observation_times":[],"source_snapshots":[now.isoformat()]}
        def outage(*args): raise RuntimeError('offline')
        c.update({"collected_at":now.isoformat()}, {}, book, fetch=outage, decided_at=now.isoformat())
        self.assertEqual(row["result"]["status"],"OPEN")
        self.assertEqual(row["result"]["entry_at"],"2026-11-05T08:00:00Z")
        self.assertEqual(row["result"]["source_evidence"],"immutable.json")

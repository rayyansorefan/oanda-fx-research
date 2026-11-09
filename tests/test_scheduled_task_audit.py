import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from scripts.scheduled_task_audit import build

class ScheduledTaskAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.now=datetime(2026,11,9,12,tzinfo=timezone.utc)
        self.roles=[{'id':f'role{i}','task_id':str(i),'minute':i*10,'receipt':f'data/task_runs/role{i}.json'} for i in range(5)]
        self.write('config/scheduled_research_roles.json',{'roles':self.roles})
    def write(self,path,data):
        p=self.root/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(data))
    def receipt(self,i,**kw):
        d={'schema':1,'role':f'role{i}','status':'COMPLETE','started_at':'2026-11-09T11:50Z','completed_at':'2026-11-09T11:55Z','source_at':'2026-11-09T11:50Z','no_broker_orders':True};d.update(kw);self.write(self.roles[i]['receipt'],d)
    def test_fresh_market_never_fabricates_completed_tasks(self):
        self.write('data/summary.json',{'collected_at':'2026-11-09T11:59Z'})
        r=build(self.root,self.now);self.assertEqual(r['task_status'],'PARTIAL_OR_NOT_RUN');self.assertFalse(any(x['status']=='COMPLETE' for x in r['roles']))
    def test_failure_receipt_is_not_success(self):
        for i in range(5):self.receipt(i)
        self.receipt(2,status='BLOCKED',blockers=['Source missing'])
        self.assertEqual(build(self.root,self.now)['roles'][2]['status'],'BLOCKED')
    def test_stale_future_and_reversed_completion_are_not_current(self):
        self.receipt(0,completed_at='2026-11-09T13:00Z')
        self.receipt(1,started_at='2026-11-08T10:00Z',completed_at='2026-11-08T10:01Z')
        self.receipt(2,completed_at='2026-11-09T11:00Z')
        r=build(self.root,self.now);self.assertEqual(r['roles'][0]['status'],'STALE_RECEIPT');self.assertEqual(r['roles'][1]['status'],'STALE_RECEIPT');self.assertEqual(r['roles'][2]['status'],'INVALID_RECEIPT')
    def test_fast_snapshot_only_covers_its_actual_pairs(self):
        self.write('data/summary.json',{'collected_at':'2026-11-09T10:00Z','pairs':{'EUR_USD':{'quote':{'time':'2026-11-09T10:00Z'}},'USD_JPY':{'quote':{'time':'2026-11-09T10:00Z'}}}})
        self.write('data/fast_latest.json',{'pairs':{'EUR_USD':{'quote':{'time':'2026-11-09T11:59Z'}}}})
        r=build(self.root,self.now);self.assertEqual(r['quote_coverage']['current'],0);self.assertEqual(set(r['fast_pair_clocks']),{'EUR_USD'})
    def test_all_genuine_current_receipts_required(self):
        for i in range(5):self.receipt(i)
        r=build(self.root,self.now);self.assertEqual(r['task_status'],'COMPLETE');self.assertFalse(r['performance_validated'])
    def test_false_order_scope_cannot_be_valid_completion(self):
        self.receipt(0,no_broker_orders=False)
        self.assertEqual(build(self.root,self.now)['roles'][0]['status'],'INVALID_RECEIPT')

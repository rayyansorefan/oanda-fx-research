"""Read-only orchestration audit. Task completion and market clocks stay separate.

No network, credentials, orders, strategy decisions or validation-rule changes.
The workflow writes one diagnostic file; only the actual scheduled role writes
its own receipt. A fresh source or a green workflow never fabricates a receipt.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc

def read(root, path, default=None):
    try:
        return json.loads((root / path).read_text())
    except (OSError, ValueError, TypeError):
        return {} if default is None else default

def moment(value):
    try:
        t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return t.astimezone(UTC) if t.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None

def clock(value, now, limit=1200):
    t = moment(value)
    if t is None or t > now:
        return {'at': value, 'status': 'INVALID', 'age_seconds': None}
    age = (now-t).total_seconds()
    return {'at': value, 'status': 'CURRENT' if age <= limit else 'STALE',
            'age_seconds': round(age, 1)}

def build(root=ROOT, now=None):
    now = now or datetime.now(UTC)
    registry = read(root, 'config/scheduled_research_roles.json')
    summary = read(root, 'data/summary.json')
    admission = read(root, 'data/signal_admission_audit.json')
    roles=[]
    for role in registry.get('roles', []):
        receipt = read(root, role['receipt'])
        stamp = clock(receipt.get('completed_at'), now, 90*60)
        status = receipt.get('status', 'NOT_RUN')
        valid = (receipt.get('role') == role['id'] and receipt.get('schema') == 1
                 and receipt.get('no_broker_orders') is True and status in
                 ('NOT_RUN', 'COMPLETE', 'PARTIAL', 'BLOCKED', 'FAILED'))
        started=moment(receipt.get('started_at'))
        completed=moment(receipt.get('completed_at'))
        if not valid or (completed and (not started or completed < started)):
            state='INVALID_RECEIPT'
        elif status == 'NOT_RUN' or not completed:
            state='NOT_RUN'
        elif stamp['status'] != 'CURRENT':
            state='STALE_RECEIPT'
        elif status != 'COMPLETE':
            state=status
        else:
            state='COMPLETE'
        roles.append({'role':role['id'], 'task_id':role['task_id'],
                      'minute':role['minute'], 'status':state,
                      'completed_at':receipt.get('completed_at'),
                      'completion_clock':stamp,
                      'source_clock':clock(receipt.get('source_at'), now),
                      'blockers':receipt.get('blockers', []),
                      'receipt':role['receipt']})
    full=clock(summary.get('collected_at'), now)
    quotes=summary.get('pairs') or {}
    if not isinstance(quotes, dict):
        quotes={}
    quote_clocks={pair:clock(row.get('quote', {}).get('time'), now)
                  for pair,row in quotes.items() if isinstance(row,dict)}
    fast=read(root, 'data/fast_latest.json')
    fast_pairs=fast.get('pairs') or {}
    fast_clocks={pair:clock(row.get('quote', {}).get('time'), now)
                 for pair,row in fast_pairs.items() if isinstance(row,dict)} if isinstance(fast_pairs,dict) else {}
    publication=read(root, 'data/public_dashboard_status.json')
    result={'schema':1, 'checked_at':now.isoformat(),
            'protocol_id':read(root, 'validation_protocol.json').get('id'),
            'scope':'GitHub FX research and simulation only',
            'task_status':'COMPLETE' if len(roles)==5 and all(r['status']=='COMPLETE' for r in roles) else 'PARTIAL_OR_NOT_RUN',
            'roles':roles, 'full_source':full,
            'quote_coverage':{'reported':len(quotes),
                              'current':sum(v['status']=='CURRENT' for v in quote_clocks.values()),
                              'stale_or_invalid':sorted(p for p,v in quote_clocks.items() if v['status']!='CURRENT')},
            'fast_pair_clocks':fast_clocks,
            'active_canonical_plans':len(read(root, 'signals.json').get('signals') or []),
            'admission':{'source_at':admission.get('source_at'), 'status':admission.get('status'), 'rejected':admission.get('rejected',{})},
            'paper_exit_study':read(root, 'data/exit_management_research.json').get('metrics',{}),
            'publication':{'status':publication.get('status'), 'snapshot_at':publication.get('snapshot_at'),
                           'same_full_source':bool(publication.get('snapshot_at')) and publication.get('snapshot_at')==summary.get('collected_at'),
                           'note':'A verified fast publication may legitimately differ; validate fast pair clocks and exporter provenance separately.'},
            'performance_validated':False, 'no_broker_orders':True,
            'note':'This audit observes persisted evidence. It does not prove task invocation, continuous monitoring, successful user delivery or profitable performance.'}
    return result

if __name__=='__main__':
    report=build()
    path=ROOT/'data/scheduled_task_health.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'task_status':report['task_status'], 'full_source':report['full_source'],
                      'quote_coverage':report['quote_coverage'], 'roles':{r['role']:r['status'] for r in report['roles']}}))

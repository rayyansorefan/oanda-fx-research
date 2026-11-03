"""Forward-only FX paper exit-management shadow study. Never submits broker orders.

The source is the EXISTING champion paper-entry ledger. Only entries and plans
created after this study's immutable start are eligible. Exit decisions use
completed, chronological OANDA bid/ask M15 bars, never a future indicator.
Research entry expiry is a thesis-review time, NOT a broker close instruction.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/exit_management_research.json'
COMPARE = ROOT / 'data/strategy_comparison.json'
RULE_VERSION = 'exit-shadow-20261103-v1'
ARM_NAMES = ('FIXED_STOP_TARGET', 'PROFIT_PROTECTION')
MAX_HOLD_BARS = 96
MIN_PAIRED_CASES = 30
UTC = timezone.utc


def moment(s):
    try:
        t = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        return t.astimezone(UTC) if t.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def iso(t):
    return t.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def numeric(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def exit_side(plan, bar):
    return bar['bid'] if plan['direction'] == 'LONG' else bar['ask']


def risk_r(plan, price):
    sign = 1 if plan['direction'] == 'LONG' else -1
    return sign * (price - plan['entry']) / abs(plan['entry'] - plan['stop'])


def valid_plan(plan):
    if plan.get('direction') not in ('LONG', 'SHORT'):
        return False
    nums = [numeric(plan.get(x)) for x in ('entry', 'stop', 'tp1', 'pip_size')]
    if any(x is None or x <= 0 for x in nums):
        return False
    entry, stop, tp, _ = nums
    return (stop < entry < tp if plan['direction'] == 'LONG' else tp < entry < stop)


def initial_arm(plan):
    return {'status': 'OPEN', 'open_fraction': 1.0, 'realised_r': 0.0,
            'stop': plan['stop'], 'last_bar_at': None, 'processed_bars': 0,
            'pending': [], 'partial_taken': False, 'stop_tightened': False,
            'expiry_reviewed': False, 'last_close': None, 'actions': [],
            'closed_at': None, 'gross_r': None}


def finish(plan, arm, price, timestamp, status):
    arm['realised_r'] += arm['open_fraction'] * risk_r(plan, price)
    arm['open_fraction'] = 0.0
    arm['status'] = status
    arm['closed_at'] = iso(timestamp)
    arm['gross_r'] = round(arm['realised_r'], 5)
    arm['pending'] = []
    return arm


def normalised_bars(raw, source_at):
    bars = []
    for row in raw:
        opened = moment(row.get('time')) if isinstance(row, dict) else None
        if opened is None or opened + timedelta(minutes=15) > source_at:
            continue
        try:
            sides = {s: {k: numeric(row[s].get(k)) for k in ('o', 'h', 'l', 'c')}
                     for s in ('bid', 'ask')}
            if any(v is None or v <= 0 for side in sides.values() for v in side.values()):
                continue
            if any(side['l'] > min(side['o'], side['c']) or
                   side['h'] < max(side['o'], side['c']) or side['h'] < side['l']
                   for side in sides.values()):
                continue
        except (KeyError, TypeError, AttributeError):
            continue
        bars.append({'t': opened, **sides})
    return sorted({b['t']: b for b in bars}.values(), key=lambda b: b['t'])


def apply_bar(plan, arm, bar, protective):
    """Apply previously decided action at next open, then evaluate the new bar.

    Never use the just-formed high/low to decide an earlier intervention.
    Within-bar stop AND TP ambiguity is unscored for either strategy.
    """
    if arm['status'] != 'OPEN':
        return arm
    t, close_time = bar['t'], bar['t'] + timedelta(minutes=15)
    q = exit_side(plan, bar)
    at_open = q['o']
    for action in list(arm['pending']):
        if action == 'EXIT':
            arm['actions'].append({'at': iso(t), 'action': 'EXIT', 'execution': 'NEXT_BAR_OPEN'})
            return finish(plan, arm, at_open, t, 'EXIT_THESIS_REASSESSMENT')
        if action == 'TAKE_PARTIAL_PROFIT' and not arm['partial_taken']:
            arm['realised_r'] += 0.5 * risk_r(plan, at_open)
            arm['open_fraction'] = 0.5
            arm['partial_taken'] = True
            arm['actions'].append({'at': iso(t), 'action': 'TAKE_PARTIAL_PROFIT',
                                   'fraction': 0.5, 'execution': 'NEXT_BAR_OPEN'})
        if action == 'TIGHTEN_STOP' and not arm['stop_tightened']:
            arm['stop'] = max(arm['stop'], plan['entry']) if plan['direction'] == 'LONG' else min(arm['stop'], plan['entry'])
            arm['stop_tightened'] = True
            arm['actions'].append({'at': iso(t), 'action': 'TIGHTEN_STOP',
                                   'stop': arm['stop'], 'execution': 'NEXT_BAR_OPEN'})
    arm['pending'] = []
    long = plan['direction'] == 'LONG'
    stop = arm['stop']
    # Stop gaps are filled adversely at the worse opening price, not the stop.
    open_stopped = (at_open <= stop if long else at_open >= stop)
    if open_stopped:
        return finish(plan, arm, at_open, t, 'STOP_GAP')
    if at_open >= plan['tp1'] if long else at_open <= plan['tp1']:
        return finish(plan, arm, plan['tp1'], t, 'TP1')
    stop_hit = q['l'] <= stop if long else q['h'] >= stop
    target_hit = q['h'] >= plan['tp1'] if long else q['l'] <= plan['tp1']
    if stop_hit and target_hit:
        arm['status'] = 'AMBIGUOUS_BAR'
        arm['closed_at'] = iso(close_time)
        arm['gross_r'] = None
        arm['pending'] = []
        return arm
    if stop_hit:
        return finish(plan, arm, stop, close_time, 'STOP')
    if target_hit:
        return finish(plan, arm, plan['tp1'], close_time, 'TP1')
    arm['processed_bars'] += 1
    arm['last_bar_at'] = iso(t)
    closing_r = risk_r(plan, q['c'])
    prior_close = arm['last_close']
    arm['last_close'] = q['c']
    if protective:
        if not arm['partial_taken'] and closing_r >= 0.75:
            arm['pending'].append('TAKE_PARTIAL_PROFIT')
        if not arm['stop_tightened'] and closing_r >= 1.0:
            arm['pending'].append('TIGHTEN_STOP')
        expiry = moment(plan['expires_at'])
        if expiry and close_time >= expiry and not arm['expiry_reviewed']:
            arm['expiry_reviewed'] = True
            reversed_bar = prior_close is not None and (q['c'] < prior_close if long else q['c'] > prior_close)
            invalid = closing_r <= 0 or (closing_r < 0.25 and reversed_bar)
            action = 'EXIT' if invalid else 'HOLD'
            arm['actions'].append({'at': iso(close_time), 'action': action,
                                   'reason': 'PLAN_EXPIRY_THESIS_REVIEW',
                                   'effective': 'NEXT_BAR_OPEN' if invalid else 'CONTINUE_MONITORING'})
            if invalid:
                arm['pending'].append('EXIT')
    if arm['processed_bars'] >= MAX_HOLD_BARS and 'EXIT' not in arm['pending']:
        arm['pending'].append('EXIT')
        arm['actions'].append({'at': iso(close_time), 'action': 'EXIT', 'reason': '96_BAR_TIME_LIMIT'})
    return arm


def progress_case(case, raw_bars, source_at):
    plan = case['plan']
    entered = moment(plan['entry_at'])
    bars = normalised_bars(raw_bars, source_at)
    if not entered or not bars:
        case['data_status'] = 'DATA_GAP'; return case
    post = [bar for bar in bars if bar['t'] > entered]
    if not post:
        case['data_status'] = 'AWAITING_NEXT_COMPLETED_BAR'; return case
    for arm_key,protective in [('FIXED_STOP_TARGET', False), ('PROFIT_PROTECTION', True)]:
        arm = case['arms'][arm_key]
        if arm['status'] != 'OPEN':
            continue  # Preserve already-scored outcomes as later M15 history rolls off.
        last = moment(arm.get('last_bar_at'))
        future = [bar for bar in post if last is None or bar['t'] > last]
        previous = last or entered
        if future and future[0]['t'] > previous + timedelta(minutes=15, seconds=1) and not (future[0]['t'] - previous >= timedelta(hours=24) and previous.weekday() in (4,5,6)):
            arm['status'] = 'DATA_GAP'; arm['gross_r'] = None; continue
        for bar in future:
            if bar['t'] - previous > timedelta(minutes=15, seconds=1):
                if not (bar['t'] - previous >= timedelta(hours=24) and previous.weekday() in (4, 5, 6)):
                    arm['status'] = 'DATA_GAP';arm['gross_r'] = None;break
            if arm['status'] != 'OPEN':
                break
            arm = apply_bar(plan, arm, bar, protective)
            arm['last_bar_at'] = iso(bar['t'])
            previous = bar['t']
        case['arms'][arm_key] = arm
    case['data_status'] = 'OK' if all(a['status'] != 'DATA_GAP' for a in case['arms'].values()) else 'DATA_GAP'
    return case


def register(book, record, registered_at):
    if record.get('policy') != 'champion':
        return False
    source, result = record.get('plan') or {}, record.get('result') or {}
    if result.get('status') not in ('OPEN', 'WIN_TP1', 'LOSS_STOP',
                                    'TIME_EXIT', 'AMBIGUOUS_EXIT_BAR'):
        return False  # Entry was not proven executable by the existing paper evaluator.
    entered = moment(result.get('entry_at'))
    created = moment(source.get('created_at'))
    study_started = moment(book['study_started_at'])
    if not entered or not created or not study_started or created < study_started or entered < study_started:
        return False
    p = {'id': source.get('id'), 'instrument': source.get('instrument'),
         'direction': source.get('direction'), 'entry_at': iso(entered),
         'entry': numeric(result.get('entry')), 'stop': numeric(source.get('stop')),
         'tp1': numeric(source.get('tp1')), 'pip_size': numeric(source.get('pip_size')) or
         (0.01 if str(source.get('instrument', '')).endswith('_JPY') else 0.0001),
         'expires_at': source.get('expires_at')}
    expiry = moment(p['expires_at'])
    if not p['id'] or not valid_plan(p) or not expiry or entered > expiry:
        return False
    key = str(p['id'])
    if key not in book['cases']:
        book['cases'][key] = {'registered_at': iso(registered_at), 'plan': p,
                             'source': 'OANDA_CHAMPION_PAPER_FILL_NOT_PEPPERSTONE',
                             'arms': {k: initial_arm(p) for k in ARM_NAMES}}
    return True


def metrics(book):
    cases = list(book['cases'].values())
    paired = [c for c in cases if all(c['arms'][arm]['gross_r'] is not None for arm in ARM_NAMES)]
    wins = sum(c['arms']['FIXED_STOP_TARGET']['gross_r'] > 0 for c in paired)
    losses = sum(c['arms']['FIXED_STOP_TARGET']['gross_r'] < 0 for c in paired)
    def performance(arm):
        values = [c['arms'][arm]['gross_r'] for c in paired]
        return {'paired_count': len(values), 'mean_ohlc_exit_side_r':
                round(sum(values)/len(values),5) if values else None,
                'total_r':round(sum(values),5) if values else None,
                'slippage_stress_pips_per_side': {str(stress):
                    (round(sum(c['arms'][arm]['gross_r'] - 2*stress*c['plan']['pip_size'] /
                               abs(c['plan']['entry']-c['plan']['stop']) for c in paired)/len(paired),5)
                     if paired else None) for stress in (0,0.25,0.5)}}
    pair_days = {(c['plan']['instrument'], c['plan']['entry_at'][:10]) for c in paired}
    return {'registered_paper_entries':len(cases), 'paired_scored':len(paired),
            'fixed_arm_wins':wins, 'fixed_arm_losses':losses,
            'independent_pair_days':len(pair_days),
            'ambiguous_or_missing':len(cases)-len(paired),
            'arms': {name:performance(name) for name in ARM_NAMES},
            'review_threshold_met':len(pair_days)>=MIN_PAIRED_CASES and wins>0 and losses>0,
            'approved_for_live_exit_advice':False}


def update(book, comparison, pair_data, now):
    """No retrospective entries: freeze immutable study start at first run."""
    if book is None:
        book = {'schema':1,'version':RULE_VERSION,'study_started_at':iso(now),'cases':{}}
    if book.get('version') != RULE_VERSION or not moment(book.get('study_started_at')):
        raise ValueError('Study rule version changed: manual migration required')
    for row in comparison.get('records', []):
        register(book, row, now)
    for case in book['cases'].values():
        if all(a['status'] != 'OPEN' for a in case['arms'].values()):
            continue
        pair = case['plan']['instrument']
        source = pair_data.get(pair) or {}
        collected = moment(source.get('collected_at'))
        if source.get('status') != 'OK' or not collected or collected > now + timedelta(minutes=5) or now - collected > timedelta(minutes=90):
            case['data_status'] = 'SOURCE_UNAVAILABLE';continue
        q = source.get('quote') or {}
        qt = moment(q.get('time'))
        if not qt or qt > collected + timedelta(minutes=5) or collected - qt > timedelta(minutes=90) or not q.get('tradeable'):
            case['data_status'] = 'QUOTE_STALE';continue
        progress_case(case, ((source.get('frames') or {}).get('M15') or {}).get('recent_completed_candles') or [],collected)
    book['last_checked_at'] = iso(now)
    book['metrics'] = metrics(book)
    book['policy'] = {'arms':list(ARM_NAMES), 'entry_source':'Existing champion PAPER fills, never assumed live Pepperstone positions',
       'rules':'At completed M15 close >=0.75R propose half-profit at NEXT bar open; >=1.0R tighten stop to OANDA entry effective next bar; at entry-plan expiry review thesis and EXIT next bar open only if adverse, else HOLD. Fixed-stop/TP baseline identical; both require post-entry completed bars.',
       'broker_access':False, 'trade_execution':False, 'retroactive_cohorts':False,
       'cost_note':'OANDA actual bid/ask exit side, with extra slippage sensitivity only; Pepperstone fills, funding and position sizing unknown.',
       'validation':'No live advice or automatic promotion; >=30 paired independent pair-days and both positive and negative cases required even to consider further review.'}
    return book


def main():
    now = datetime.now(UTC)
    comparison = json.loads(COMPARE.read_text()) if COMPARE.exists() else {'records':[]}
    book = json.loads(OUT.read_text()) if OUT.exists() else None
    pairs = {c['plan']['instrument'] for c in (book or {}).get('cases', {}).values()}
    for row in comparison.get('records', []):
        p = row.get('plan') or {};r = row.get('result') or {}
        if row.get('policy') == 'champion' and r.get('entry_at') and p.get('instrument'):
            pairs.add(p['instrument'])
    pair_data = {}
    for pair in pairs:
        if not pair.replace('_','').isalpha():continue
        path = ROOT/'data'/'pairs'/(pair+'.json')
        try:pair_data[pair] = json.loads(path.read_text())
        except (ValueError,OSError):pass
    result = update(book, comparison, pair_data, now)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print('Exit management SHADOW ONLY:',json.dumps(result['metrics'],sort_keys=True))


if __name__ == '__main__':
    main()

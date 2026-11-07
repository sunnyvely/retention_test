import csv
import math
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data' / 'processed' / 'events_asof_2015-09-15_21-00_UTC_with_categories.csv'
OUT = ROOT / 'data' / 'processed'
CUTOFF_MS = 1442350800000  # 2015-09-15 21:00 UTC, inclusive
WINDOW_MS = 30 * 24 * 60 * 60 * 1000
MAX_ANCHOR_MS = CUTOFF_MS - WINDOW_MS
MISSING = '__MISSING__'

with tempfile.NamedTemporaryFile(prefix='retention_', suffix='.sqlite', delete=False) as tmp:
    db_path = Path(tmp.name)
conn = sqlite3.connect(db_path)
aggregates = {}
first_view_summary = defaultdict(lambda: {'cohort': 0, 'returned': 0})


def entity_label(scope, value):
    if value == MISSING:
        return {'root': '대분류 없음', 'category': '카테고리 없음', 'item': '아이템 없음'}.get(scope, '전체')
    return str(value)


def new_state():
    return {
        'any_anchor': None, 'any_anchor_event': None, 'any_anchor_txid': None, 'any_return': None,
        'purchase_anchor': None, 'purchase_txid': None,
        'repurchase_return': None, 'buyer_any_return': None,
        'view_anchor': None, 'view_return': None,
    }


def day_index(anchor, returned_at):
    if returned_at is None or returned_at <= anchor:
        return None
    delta = returned_at - anchor
    return min(30, math.ceil(delta / 86_400_000))


def add_state(scope, entity, state):
    key = (scope, entity_label(scope, entity))
    agg = aggregates.setdefault(key, {
        'any_cohort': 0, 'any_returned': 0, 'any_days': Counter(),
        'purchase_cohort': 0, 'repurchased': 0, 'repurchase_days': Counter(),
        'buyer_cohort': 0, 'buyer_returned': 0, 'buyer_return_days': Counter(),
    })
    if state['any_anchor'] is not None and state['any_anchor'] <= MAX_ANCHOR_MS:
        agg['any_cohort'] += 1
        day = day_index(state['any_anchor'], state['any_return'])
        if day is not None:
            agg['any_returned'] += 1
            agg['any_days'][day] += 1
    if state['purchase_anchor'] is not None and state['purchase_anchor'] <= MAX_ANCHOR_MS:
        agg['purchase_cohort'] += 1
        agg['buyer_cohort'] += 1
        day = day_index(state['purchase_anchor'], state['repurchase_return'])
        if day is not None:
            agg['repurchased'] += 1
            agg['repurchase_days'][day] += 1
        day = day_index(state['purchase_anchor'], state['buyer_any_return'])
        if day is not None:
            agg['buyer_returned'] += 1
            agg['buyer_return_days'][day] += 1


try:
    conn.execute('PRAGMA journal_mode=OFF')
    conn.execute('PRAGMA synchronous=OFF')
    conn.execute('CREATE TABLE events (visitorid TEXT NOT NULL, timestamp INTEGER NOT NULL, event TEXT NOT NULL, transactionid TEXT, itemid TEXT, categoryid TEXT, root_categoryid TEXT)')
    batch = []
    with SOURCE.open(newline='', encoding='utf-8-sig') as src:
        for row in csv.DictReader(src):
            batch.append((
                row['visitorid'], int(row['timestamp']), row['event'], row['transactionid'] or None,
                row['itemid'] or MISSING, row['categoryid'] or MISSING, row['root_categoryid'] or MISSING,
            ))
            if len(batch) >= 50000:
                conn.executemany('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)', batch)
                batch.clear()
    if batch:
        conn.executemany('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)', batch)
    conn.commit()
    conn.execute('CREATE INDEX events_by_visitor_time ON events(visitorid, timestamp)')

    current_visitor = None
    overall = new_state()
    root_states = {}
    category_states = {}
    first_global_view = None

    def finalize_visitor():
        if current_visitor is None:
            return
        add_state('overall', '전체', overall)
        for entity, state in root_states.items():
            add_state('root', entity, state)
        for entity, state in category_states.items():
            add_state('category', entity, state)
        if first_global_view is not None and first_global_view['timestamp'] <= MAX_ANCHOR_MS:
            first_view_summary[('overall', '전체')]['cohort'] += 1
            if day_index(first_global_view['timestamp'], first_global_view['return_any_timestamp']) is not None:
                first_view_summary[('overall', '전체')]['returned'] += 1
            for scope, field in [('root', 'root_categoryid'), ('category', 'categoryid'), ('item', 'itemid')]:
                key = (scope, entity_label(scope, first_global_view[field]))
                first_view_summary[key]['cohort'] += 1
                returned_at = first_global_view['return_same_timestamp']
                if day_index(first_global_view['timestamp'], returned_at) is not None:
                    first_view_summary[key]['returned'] += 1

    for visitorid, timestamp, event, transactionid, itemid, categoryid, root_categoryid in conn.execute(
        'SELECT visitorid, timestamp, event, transactionid, itemid, categoryid, root_categoryid FROM events ORDER BY visitorid, timestamp'
    ):
        if visitorid != current_visitor:
            finalize_visitor()
            current_visitor = visitorid
            overall = new_state()
            root_states = {}
            category_states = {}
            first_global_view = None

        group_states = [('overall', '전체', overall), ('root', root_categoryid, root_states.setdefault(root_categoryid, new_state())), ('category', categoryid, category_states.setdefault(categoryid, new_state()))]
        for scope, entity, state in group_states:
            if state['any_anchor'] is None:
                state['any_anchor'] = timestamp
                state['any_anchor_event'] = event
                state['any_anchor_txid'] = transactionid if event == 'transaction' else None
            elif state['any_return'] is None and timestamp > state['any_anchor'] and timestamp <= state['any_anchor'] + WINDOW_MS:
                same_purchase_line = state['any_anchor_event'] == 'transaction' and event == 'transaction' and transactionid == state['any_anchor_txid']
                if not same_purchase_line:
                    state['any_return'] = timestamp

            if event == 'transaction':
                if state['purchase_anchor'] is None:
                    state['purchase_anchor'] = timestamp
                    state['purchase_txid'] = transactionid
                elif (
                    state['repurchase_return'] is None
                    and transactionid
                    and transactionid != state['purchase_txid']
                    and timestamp > state['purchase_anchor']
                    and timestamp <= state['purchase_anchor'] + WINDOW_MS
                ):
                    state['repurchase_return'] = timestamp

            if state['purchase_anchor'] is not None and state['buyer_any_return'] is None:
                same_purchase_line = event == 'transaction' and transactionid == state['purchase_txid']
                if not same_purchase_line and timestamp > state['purchase_anchor'] and timestamp <= state['purchase_anchor'] + WINDOW_MS:
                    state['buyer_any_return'] = timestamp

            if event == 'view' and state['view_anchor'] is None:
                state['view_anchor'] = timestamp
            elif state['view_anchor'] is not None and state['view_return'] is None and timestamp > state['view_anchor'] and timestamp <= state['view_anchor'] + WINDOW_MS:
                state['view_return'] = timestamp

        if event == 'view':
            if first_global_view is None:
                first_global_view = {
                    'timestamp': timestamp,
                    'itemid': itemid,
                    'categoryid': categoryid,
                    'root_categoryid': root_categoryid,
                    'return_any_timestamp': None,
                    'return_same_timestamp': None,
                }
        if first_global_view is not None and timestamp > first_global_view['timestamp'] and timestamp <= first_global_view['timestamp'] + WINDOW_MS:
            if first_global_view['return_any_timestamp'] is None:
                first_global_view['return_any_timestamp'] = timestamp
            same_entity = (
                itemid == first_global_view['itemid']
                and categoryid == first_global_view['categoryid']
                and root_categoryid == first_global_view['root_categoryid']
            )
            if same_entity and first_global_view['return_same_timestamp'] is None:
                first_global_view['return_same_timestamp'] = timestamp

    finalize_visitor()
finally:
    conn.close()
    db_path.unlink(missing_ok=True)

summary_rows = []
curve_rows = []
for (scope, entity_id), agg in aggregates.items():
    any_rate = agg['any_returned'] / agg['any_cohort'] if agg['any_cohort'] else 0
    repurchase_rate = agg['repurchased'] / agg['purchase_cohort'] if agg['purchase_cohort'] else 0
    buyer_return_rate = agg['buyer_returned'] / agg['buyer_cohort'] if agg['buyer_cohort'] else 0
    summary_rows.append({
        'scope': scope,
        'entity_id': entity_id,
        'all_event_cohort_users': agg['any_cohort'],
        'all_event_revisitors_30d': agg['any_returned'],
        'all_event_revisit_rate_30d': any_rate,
        'purchase_cohort_users': agg['purchase_cohort'],
        'repeat_purchasers_30d': agg['repurchased'],
        'repurchase_rate_30d': repurchase_rate,
        'purchaser_revisitors_30d': agg['buyer_returned'],
        'purchaser_revisit_rate_30d': buyer_return_rate,
    })
    metric_details = [
        ('all_event_revisit', agg['any_cohort'], agg['any_days']),
        ('repurchase', agg['purchase_cohort'], agg['repurchase_days']),
        ('purchaser_revisit', agg['buyer_cohort'], agg['buyer_return_days']),
    ]
    for metric, cohort_users, day_counts in metric_details:
        running = 0
        for day in range(1, 31):
            running += day_counts.get(day, 0)
            curve_rows.append({
                'scope': scope,
                'entity_id': entity_id,
                'metric': metric,
                'day': day,
                'cohort_users': cohort_users,
                'returning_users_by_day': running,
                'retention_rate': running / cohort_users if cohort_users else 0,
            })

OUT.mkdir(parents=True, exist_ok=True)
summary_path = OUT / 'retention_30d_summary.csv'
curves_path = OUT / 'retention_30d_curves.csv'
first_view_path = OUT / 'first_view_revisit_30d.csv'
with summary_path.open('w', newline='', encoding='utf-8-sig') as f:
    writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
    writer.writeheader(); writer.writerows(summary_rows)
with curves_path.open('w', newline='', encoding='utf-8-sig') as f:
    writer = csv.DictWriter(f, fieldnames=list(curve_rows[0].keys()))
    writer.writeheader(); writer.writerows(curve_rows)
first_rows = []
for (scope, entity_id), values in first_view_summary.items():
    first_rows.append({
        'scope': scope,
        'entity_id': entity_id,
        'eligible_first_view_users': values['cohort'],
        'revisiting_users_30d': values['returned'],
        'first_view_revisit_rate_30d': values['returned'] / values['cohort'] if values['cohort'] else 0,
    })
with first_view_path.open('w', newline='', encoding='utf-8-sig') as f:
    writer = csv.DictWriter(f, fieldnames=list(first_rows[0].keys()))
    writer.writeheader(); writer.writerows(first_rows)

print('Eligible anchor cutoff:', pd_timestamp := __import__('datetime').datetime.fromtimestamp(MAX_ANCHOR_MS / 1000, __import__('datetime').timezone.utc).isoformat())
print('Overall 30-day retention:', next(row for row in summary_rows if row['scope'] == 'overall'))
print('Root groups:', sum(row['scope'] == 'root' for row in summary_rows), 'category groups:', sum(row['scope'] == 'category' for row in summary_rows))
print('Summary:', summary_path)
print('Curves:', curves_path)
print('First-view category retention:', first_view_path)

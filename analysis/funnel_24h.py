import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data' / 'processed' / 'events_asof_2015-09-15_21-00_UTC_with_categories.csv'
OUT_DIR = ROOT / 'data' / 'processed'
CUTOFF_MS = 1442350800000  # 2015-09-15 21:00 UTC, inclusive
WINDOW_MS = 24 * 60 * 60 * 1000
MAX_ANCHOR_MS = CUTOFF_MS - WINDOW_MS
BUCKETS = ['0–1분', '1–5분', '5–15분', '15–60분', '1–3시간', '3–6시간', '6–12시간', '12–24시간']
BIN_EDGES_MS = [0, 60_000, 300_000, 900_000, 3_600_000, 10_800_000, 21_600_000, 43_200_000, WINDOW_MS]


def group_counts(frame, key, column_name):
    if key is None:
        return pd.Series([len(frame)], index=['전체'], name=column_name, dtype='int64')
    return frame.groupby('_entity_key', observed=True).size().rename(column_name)


def build_scope(events, scope, entity_col=None):
    keys = ['visitorid'] + ([entity_col] if entity_col else [])
    views = events.loc[events['event'] == 'view', keys + ['timestamp']]
    anchors = (
        views.loc[views['timestamp'] <= MAX_ANCHOR_MS]
        .groupby(keys, as_index=False, dropna=False, observed=True)['timestamp']
        .min()
        .rename(columns={'timestamp': 'view_ts'})
    )
    anchors['window_end'] = anchors['view_ts'] + WINDOW_MS

    view_rows = views.rename(columns={'timestamp': 'view_event_ts'})
    view_window = anchors.merge(view_rows, on=keys, how='inner')
    view_window = view_window.loc[(view_window['view_event_ts'] >= view_window['view_ts']) & (view_window['view_event_ts'] <= view_window['window_end'])]

    cart_rows = events.loc[events['event'] == 'addtocart', keys + ['timestamp']].rename(columns={'timestamp': 'cart_ts'})
    cart_candidates = anchors.merge(cart_rows, on=keys, how='inner')
    cart_candidates = cart_candidates.loc[(cart_candidates['cart_ts'] > cart_candidates['view_ts']) & (cart_candidates['cart_ts'] <= cart_candidates['window_end'])]
    cart_first = cart_candidates.sort_values('cart_ts').drop_duplicates(keys, keep='first')

    transaction_rows = events.loc[events['event'] == 'transaction', keys + ['timestamp']].rename(columns={'timestamp': 'transaction_ts'})
    purchase_candidates = cart_first.merge(transaction_rows, on=keys, how='inner')
    purchase_candidates = purchase_candidates.loc[(purchase_candidates['transaction_ts'] > purchase_candidates['cart_ts']) & (purchase_candidates['transaction_ts'] <= purchase_candidates['window_end'])]
    purchase_first = purchase_candidates.sort_values('transaction_ts').drop_duplicates(keys, keep='first')

    if entity_col is not None:
        missing_key = '__MISSING_CATEGORY__'
        for frame in [anchors, view_window, cart_first, cart_candidates, purchase_first, purchase_candidates]:
            frame['_entity_key'] = frame[entity_col].astype('string').fillna(missing_key)

    if entity_col is None:
        summary = pd.DataFrame({
            'entity_id': ['전체'],
            'view_users': [len(anchors)],
            'view_events': [len(view_window)],
            'cart_users': [len(cart_first)],
            'cart_events': [len(cart_candidates)],
            'purchase_users': [len(purchase_first)],
            'transaction_events': [len(purchase_candidates)],
        })
    else:
        summary = anchors.groupby('_entity_key', observed=True).size().rename('view_users').to_frame()
        for frame, column in [(view_window, 'view_events'), (cart_first, 'cart_users'), (cart_candidates, 'cart_events'), (purchase_first, 'purchase_users'), (purchase_candidates, 'transaction_events')]:
            summary[column] = group_counts(frame, entity_col, column).reindex(summary.index, fill_value=0)
        summary = summary.reset_index().rename(columns={'_entity_key': 'entity_id'})
        missing_label = '대분류 없음' if entity_col == 'root_categoryid' else '카테고리 없음'
        summary['entity_id'] = summary['entity_id'].replace({'__MISSING_CATEGORY__': missing_label})

    summary['view_to_cart_rate'] = summary['cart_users'] / summary['view_users']
    summary['cart_to_purchase_rate'] = summary['purchase_users'] / summary['cart_users'].replace(0, pd.NA)
    summary['view_to_purchase_rate'] = summary['purchase_users'] / summary['view_users']
    summary['scope'] = scope

    distribution_parts = []
    for transition, frame, duration_col, end_col in [
        ('View→AddToCart', cart_first, 'view_to_cart_ms', 'cart_ts'),
        ('AddToCart→Transaction', purchase_first, 'cart_to_transaction_ms', 'transaction_ts'),
    ]:
        part = frame.copy()
        part[duration_col] = part[end_col] - (part['view_ts'] if transition == 'View→AddToCart' else part['cart_ts'])
        part['time_bucket'] = pd.cut(part[duration_col], BIN_EDGES_MS, labels=BUCKETS, right=True, include_lowest=False)
        if entity_col is None:
            part['entity_id'] = '전체'
            grouped = part.groupby('time_bucket', observed=False).size().rename('visitors').reset_index()
            grouped['entity_id'] = '전체'
        else:
            part['entity_id'] = part['_entity_key'].replace({'__MISSING_CATEGORY__': '대분류 없음' if entity_col == 'root_categoryid' else '카테고리 없음'})
            grouped = part.groupby(['entity_id', 'time_bucket'], observed=False).size().rename('visitors').reset_index()
        grouped['scope'] = scope
        grouped['transition'] = transition
        distribution_parts.append(grouped)
    distribution = pd.concat(distribution_parts, ignore_index=True)
    return summary, distribution


events = pd.read_csv(
    SOURCE,
    usecols=['timestamp', 'visitorid', 'event', 'root_categoryid', 'categoryid'],
    dtype={
        'timestamp': 'int64',
        'visitorid': 'int64',
        'event': 'category',
        'root_categoryid': 'Int64',
        'categoryid': 'Int64',
    },
)
category_root_map = (
    events.loc[events['categoryid'].notna() & events['root_categoryid'].notna(), ['categoryid', 'root_categoryid']]
    .drop_duplicates()
    .sort_values(['root_categoryid', 'categoryid'])
    .rename(columns={'categoryid': 'category_id', 'root_categoryid': 'root_category_id'})
)
category_root_map.to_csv(OUT_DIR / 'funnel_category_root_map.csv', index=False, encoding='utf-8-sig')
all_summaries = []
all_distributions = []
for scope, entity_col in [('overall', None), ('root', 'root_categoryid'), ('category', 'categoryid')]:
    summary, distribution = build_scope(events, scope, entity_col)
    all_summaries.append(summary)
    all_distributions.append(distribution)

summary_all = pd.concat(all_summaries, ignore_index=True)
distribution_all = pd.concat(all_distributions, ignore_index=True)
summary_all.to_csv(OUT_DIR / 'funnel_24h_summary.csv', index=False, encoding='utf-8-sig')
distribution_all.to_csv(OUT_DIR / 'funnel_24h_transition_distribution.csv', index=False, encoding='utf-8-sig')

print('Anchor view cutoff:', pd.to_datetime(MAX_ANCHOR_MS, unit='ms', utc=True))
print(summary_all.loc[summary_all['scope'] == 'overall', ['view_users', 'view_events', 'cart_users', 'cart_events', 'purchase_users', 'transaction_events', 'view_to_cart_rate', 'cart_to_purchase_rate', 'view_to_purchase_rate']].to_string(index=False))
print('Root groups:', (summary_all['scope'] == 'root').sum(), 'category groups:', (summary_all['scope'] == 'category').sum())
print('Summary:', OUT_DIR / 'funnel_24h_summary.csv')
print('Transition distributions:', OUT_DIR / 'funnel_24h_transition_distribution.csv')
print('Category root map:', OUT_DIR / 'funnel_category_root_map.csv')

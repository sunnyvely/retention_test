import csv
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data' / 'processed' / 'events_asof_2015-09-15_21-00_UTC_deduplicated.csv'
OUTPUT = ROOT / 'data' / 'processed' / 'visitor_summary.csv'

with tempfile.NamedTemporaryFile(prefix='visitor_summary_', suffix='.sqlite', delete=False) as tmp:
    db_path = Path(tmp.name)
conn = sqlite3.connect(db_path)
try:
    conn.execute('PRAGMA journal_mode=OFF')
    conn.execute('PRAGMA synchronous=OFF')
    conn.execute('CREATE TABLE events (visitorid TEXT NOT NULL, timestamp INTEGER NOT NULL, event TEXT NOT NULL, transactionid TEXT)')
    batch = []
    with SOURCE.open(newline='', encoding='utf-8') as src:
        for row in csv.DictReader(src):
            batch.append((row['visitorid'], int(row['timestamp']), row['event'], row['transactionid'] or None))
            if len(batch) >= 50000:
                conn.executemany('INSERT INTO events VALUES (?, ?, ?, ?)', batch)
                batch.clear()
    if batch:
        conn.executemany('INSERT INTO events VALUES (?, ?, ?, ?)', batch)
    conn.commit()

    query = '''
        SELECT visitorid,
               MIN(timestamp) AS first_visit_ts,
               MAX(CASE WHEN event = 'transaction' THEN timestamp END) AS last_purchase_ts,
               COUNT(DISTINCT CASE WHEN event = 'transaction' THEN NULLIF(transactionid, '') END) AS purchase_count,
               SUM(CASE WHEN event = 'view' THEN 1 ELSE 0 END) AS view_count,
               SUM(CASE WHEN event = 'addtocart' THEN 1 ELSE 0 END) AS cart_count
        FROM events
        GROUP BY visitorid
        ORDER BY visitorid
    '''
    cursor = conn.execute(query)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('w', newline='', encoding='utf-8-sig') as dst:
        writer = csv.writer(dst)
        writer.writerow(['id', 'purchase_count', 'purchase_total_amount', 'first_visit_date', 'last_purchase_date', 'view_count', 'cart_count'])
        visitors = purchasers = total_purchases = total_views = total_carts = 0
        for visitorid, first_ts, last_ts, purchase_count, view_count, cart_count in cursor:
            date_first = datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
            date_last = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d') if last_ts is not None else ''
            writer.writerow([visitorid, purchase_count, '', date_first, date_last, view_count, cart_count])
            visitors += 1
            purchasers += purchase_count > 0
            total_purchases += purchase_count
            total_views += view_count
            total_carts += cart_count
    print(f'Visitors (rows): {visitors:,}')
    print(f'Visitors with purchases: {purchasers:,}')
    print(f'Distinct purchases: {total_purchases:,}')
    print(f'Views: {total_views:,}; add-to-cart events: {total_carts:,}')
    print('purchase_total_amount: blank for all rows; amount/price is absent from source data')
    print(f'Output: {OUTPUT}')
finally:
    conn.close()
    db_path.unlink(missing_ok=True)

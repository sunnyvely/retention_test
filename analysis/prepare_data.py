import csv
from datetime import datetime, timezone
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data' / 'raw' / 'events.csv'
OUTPUT = ROOT / 'data' / 'processed' / 'events_asof_2015-09-15_21-00_UTC_deduplicated.csv'
CUTOFF_MS = 1442350800000  # 2015-09-15 21:00:00 UTC
WEEKDAYS_KO = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']

seen = set()
read_rows = 0
kept_rows = 0
post_cutoff_rows = 0
duplicates = 0
with SOURCE.open('r', newline='', encoding='utf-8') as src, OUTPUT.open('w', newline='', encoding='utf-8') as dst:
    reader = csv.DictReader(src)
    fieldnames = reader.fieldnames + ['year', 'month', 'day', 'weekday', 'day_type']
    writer = csv.DictWriter(dst, fieldnames=fieldnames)
    writer.writeheader()
    for row in reader:
        read_rows += 1
        if int(row['timestamp']) > CUTOFF_MS:
            post_cutoff_rows += 1
            continue
        # Hash the complete CSV row including timestamp; only exact row matches are duplicates.
        key_bytes = '\x1f'.join(row[name] for name in reader.fieldnames).encode('utf-8')
        digest = hashlib.sha256(key_bytes).digest()
        if digest in seen:
            duplicates += 1
            continue
        seen.add(digest)
        date_utc = datetime.fromtimestamp(int(row['timestamp']) / 1000, tz=timezone.utc)
        row['year'] = date_utc.year
        row['month'] = date_utc.month
        row['day'] = date_utc.day
        row['weekday'] = WEEKDAYS_KO[date_utc.weekday()]
        row['day_type'] = '주말' if date_utc.weekday() >= 5 else '평일'
        writer.writerow(row)
        kept_rows += 1
print(f'Input rows: {read_rows:,}')
print(f'Rows after cutoff: {read_rows - post_cutoff_rows:,}')
print(f'Rows after exact deduplication: {kept_rows:,}')
print(f'Exact duplicate rows removed: {duplicates:,}')
print(f'Rows after cutoff excluded: {post_cutoff_rows:,}')
print(f'Output: {OUTPUT}')

import csv
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / 'data' / 'processed' / 'events_asof_2015-09-15_21-00_UTC_deduplicated.csv'
HISTORY = ROOT / 'data' / 'raw' / 'item_category_history.csv'
OUTPUT = ROOT / 'data' / 'processed' / 'events_asof_2015-09-15_21-00_UTC_with_categories.csv'

# Build per-item temporal histories, then attach the most recent record no later than each event.
history = defaultdict(list)
with HISTORY.open(newline='', encoding='utf-8-sig') as src:
    for row in csv.DictReader(src):
        history[row['itemid']].append((
            int(row['effective_timestamp']),
            row['categoryid'],
            row['root_categoryid'],
        ))
for item_history in history.values():
    item_history.sort(key=lambda row: row[0])

rows = matches = 0
with EVENTS.open(newline='', encoding='utf-8-sig') as src, OUTPUT.open('w', newline='', encoding='utf-8-sig') as dst:
    reader = csv.DictReader(src)
    fieldnames = reader.fieldnames + ['effective_timestamp', 'categoryid', 'root_categoryid']
    writer = csv.DictWriter(dst, fieldnames=fieldnames)
    writer.writeheader()
    for event in reader:
        rows += 1
        item_history = history.get(event['itemid'], ())
        if item_history:
            pos = bisect_right(item_history, (int(event['timestamp']), '\uffff', '\uffff')) - 1
        else:
            pos = -1
        if pos >= 0:
            effective_timestamp, categoryid, root_categoryid = item_history[pos]
            event['effective_timestamp'] = effective_timestamp
            event['categoryid'] = categoryid
            event['root_categoryid'] = root_categoryid
            matches += 1
        else:
            event['effective_timestamp'] = ''
            event['categoryid'] = ''
            event['root_categoryid'] = ''
        writer.writerow(event)

print(f'Input event rows: {rows:,}')
print(f'Rows with category history at event time: {matches:,}')
print(f'Rows without eligible category history: {rows - matches:,}')
print(f'Match rate: {matches / rows:.2%}')
print(f'Output rows (left join): {rows:,}')
print(f'Output: {OUTPUT}')

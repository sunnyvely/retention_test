import csv
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data' / 'processed' / 'events_asof_2015-09-15_21-00_UTC_deduplicated.csv'
FIGURE = ROOT / 'outputs' / 'figures' / 'retention_nday.png'
SUMMARY = ROOT / 'outputs' / 'tables' / 'retention_nday.csv'

with tempfile.NamedTemporaryFile(prefix='retention_', suffix='.sqlite', delete=False) as tmp:
    db_path = Path(tmp.name)
conn = sqlite3.connect(db_path)
try:
    conn.execute('CREATE TABLE events (visitorid TEXT NOT NULL, event_date TEXT NOT NULL)')
    batch = []
    with SOURCE.open(newline='', encoding='utf-8-sig') as src:
        for row in csv.DictReader(src):
            date = datetime.fromtimestamp(int(row['timestamp']) / 1000, tz=timezone.utc).date().isoformat()
            batch.append((row['visitorid'], date))
            if len(batch) >= 50000:
                conn.executemany('INSERT INTO events VALUES (?, ?)', batch)
                batch.clear()
    if batch:
        conn.executemany('INSERT INTO events VALUES (?, ?)', batch)
    conn.commit()
    conn.execute('CREATE INDEX events_by_visitor_date ON events(visitorid, event_date)')
    conn.execute('CREATE TABLE first_days AS SELECT visitorid, MIN(event_date) AS first_date FROM events GROUP BY visitorid')
    conn.execute('CREATE INDEX first_days_by_visitor ON first_days(visitorid)')
    conn.execute('CREATE TABLE user_days AS SELECT DISTINCT visitorid, event_date FROM events')
    conn.execute('CREATE INDEX user_days_by_visitor_date ON user_days(visitorid, event_date)')
    last_date = date

    first_dates = dict(conn.execute('SELECT first_date, COUNT(*) FROM first_days GROUP BY first_date'))
    returned_by_day = dict(conn.execute(
        "SELECT CAST(julianday(d.event_date)-julianday(f.first_date) AS INTEGER), COUNT(*) "
        "FROM first_days f JOIN user_days d ON d.visitorid=f.visitorid "
        "WHERE d.event_date >= f.first_date GROUP BY 1"
    ))
    start_date = min(first_dates)
    max_day = (datetime.fromisoformat(last_date).date() - datetime.fromisoformat(start_date).date()).days
    rows = []
    for nday in range(max_day + 1):
        # Include only cohorts whose Nth calendar day falls within observed data.
        eligible_through = (datetime.fromisoformat(last_date).date()).toordinal() - nday
        cohort_size = sum(size for day, size in first_dates.items() if datetime.fromisoformat(day).date().toordinal() <= eligible_through)
        returned = returned_by_day.get(nday, 0)
        rate = returned / cohort_size if cohort_size else 0
        rows.append((nday, cohort_size, returned, rate))
finally:
    conn.close()
    db_path.unlink(missing_ok=True)

SUMMARY.parent.mkdir(parents=True, exist_ok=True)
with SUMMARY.open('w', newline='', encoding='utf-8-sig') as out:
    writer = csv.writer(out)
    writer.writerow(['nday', 'eligible_cohort_users', 'returning_users', 'retention_rate'])
    writer.writerows(rows)

plt.rcParams['font.family'] = ['Malgun Gothic', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
fig, ax = plt.subplots(figsize=(11, 6.2))
x = [r[0] for r in rows]
y = [r[3] for r in rows]
ax.plot(x, y, color='#2563EB', linewidth=2.5, marker='o', markersize=4)
ax.fill_between(x, y, color='#3B82F6', alpha=0.10)
ax.set_xlabel('N일차 (nday)')
ax.set_ylabel('재방문 율')
ax.yaxis.set_major_formatter(PercentFormatter(1))
ax.set_xlim(left=0)
ax.set_ylim(bottom=0)
ax.set_title('N-day 사용자 리텐션 곡선')
ax.grid(True, alpha=0.25)
ax.set_axisbelow(True)
fig.text(0.01, 0.01, f'기준: 사용자별 첫 이벤트 UTC 날짜 코호트 · 해당 N일차에 이벤트가 있는 고유 사용자 / 관측 가능한 코호트 사용자 · 관측 종료일 {last_date} UTC', fontsize=8, color='#555')
fig.tight_layout(rect=(0, 0.05, 1, 1))
FIGURE.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(FIGURE, dpi=180, bbox_inches='tight')
print(f'Observed through: {last_date} UTC')
print(f'Unique users: {rows[0][1]:,}')
print(f'Day 1 retention: {rows[1][3]:.2%} ({rows[1][2]:,}/{rows[1][1]:,})')
print(f'Max N-day: {rows[-1][0]}')
print(f'Figure: {FIGURE}')
print(f'Summary: {SUMMARY}')

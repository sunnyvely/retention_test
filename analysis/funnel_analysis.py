import csv
import sqlite3
import tempfile
from pathlib import Path
from matplotlib.ticker import FuncFormatter

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data' / 'processed' / 'events_asof_2015-09-15_21-00_UTC_deduplicated.csv'
FIGURE = ROOT / 'outputs' / 'figures' / 'funnel_sequential.png'
SUMMARY = ROOT / 'outputs' / 'tables' / 'funnel_summary.csv'

with tempfile.NamedTemporaryFile(prefix='funnel_', suffix='.sqlite', delete=False) as tmp:
    db_path = Path(tmp.name)
conn = sqlite3.connect(db_path)
try:
    conn.execute('PRAGMA journal_mode=OFF')
    conn.execute('PRAGMA synchronous=OFF')
    conn.execute('CREATE TABLE events (visitorid TEXT NOT NULL, timestamp INTEGER NOT NULL, event TEXT NOT NULL)')
    batch = []
    with SOURCE.open(newline='', encoding='utf-8-sig') as src:
        for row in csv.DictReader(src):
            batch.append((row['visitorid'], int(row['timestamp']), row['event']))
            if len(batch) >= 50000:
                conn.executemany('INSERT INTO events VALUES (?, ?, ?)', batch)
                batch.clear()
    if batch:
        conn.executemany('INSERT INTO events VALUES (?, ?, ?)', batch)
    conn.commit()
    conn.execute('CREATE INDEX events_by_visitor_time ON events(visitorid, timestamp)')

    counts = [0, 0, 0]
    current_visitor = None
    view_time = None
    cart_time = None
    purchased_after_cart = False

    def finalize_visitor():
        if view_time is not None:
            counts[0] += 1
        if cart_time is not None:
            counts[1] += 1
        if purchased_after_cart:
            counts[2] += 1

    for visitorid, timestamp, event in conn.execute(
        'SELECT visitorid, timestamp, event FROM events ORDER BY visitorid, timestamp'
    ):
        if visitorid != current_visitor:
            if current_visitor is not None:
                finalize_visitor()
            current_visitor = visitorid
            view_time = None
            cart_time = None
            purchased_after_cart = False

        if event == 'view' and view_time is None:
            view_time = timestamp
        elif event == 'addtocart' and view_time is not None and cart_time is None and timestamp > view_time:
            cart_time = timestamp
        elif event == 'transaction' and cart_time is not None and timestamp > cart_time:
            purchased_after_cart = True
    if current_visitor is not None:
        finalize_visitor()
finally:
    conn.close()
    db_path.unlink(missing_ok=True)

step_rates = [None, counts[1] / counts[0] if counts[0] else 0, counts[2] / counts[1] if counts[1] else 0]
overall_rate = counts[2] / counts[0] if counts[0] else 0
stages = ['View', 'AddToCart', 'Transaction']

SUMMARY.parent.mkdir(parents=True, exist_ok=True)
with SUMMARY.open('w', newline='', encoding='utf-8-sig') as out:
    writer = csv.writer(out)
    writer.writerow(['stage', 'unique_visitors', 'conversion_from_previous', 'overall_conversion_from_view', 'dropoff_from_previous'])
    for i, (stage, count) in enumerate(zip(stages, counts)):
        writer.writerow([
            stage,
            count,
            '' if i == 0 else f'{step_rates[i]:.8f}',
            f'{(count / counts[0]) if counts[0] else 0:.8f}',
            '' if i == 0 else counts[i - 1] - count,
        ])

plt.rcParams['font.family'] = ['Malgun Gothic', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
fig = plt.figure(figsize=(12, 6.2))
grid = fig.add_gridspec(1, 2, width_ratios=[3.2, 2.0], wspace=0.14)
ax = fig.add_subplot(grid[0, 0])
info = fig.add_subplot(grid[0, 1])
colors = ['#3B82F6', '#F59E0B', '#16A34A']
ypos = [2, 1, 0]
bars = ax.barh(ypos, counts, color=colors, height=0.55)
ax.set_yticks(ypos, stages)
ax.set_xlim(0, max(counts[0] * 1.16, 1))
ax.set_xlabel('고유 방문자 수')
ax.set_title('단계별 도달 방문자')
ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{int(value):,}'))
ax.grid(axis='x', alpha=0.2)
ax.set_axisbelow(True)
for bar, value in zip(bars, counts):
    ax.text(value + counts[0] * 0.012, bar.get_y() + bar.get_height() / 2, f'{value:,}', va='center', fontsize=10)

info.axis('off')
info.set_title('단계별 전환율', loc='left', pad=14)
info.text(0.02, 0.80, f'View → AddToCart\n{step_rates[1]:.2%}  ·  {counts[1]:,} / {counts[0]:,}', transform=info.transAxes, fontsize=12, va='top')
info.text(0.02, 0.52, f'AddToCart → Transaction\n{step_rates[2]:.2%}  ·  {counts[2]:,} / {counts[1]:,}', transform=info.transAxes, fontsize=12, va='top')
info.text(0.02, 0.24, f'View → Transaction (전체)\n{overall_rate:.2%}  ·  {counts[2]:,} / {counts[0]:,}', transform=info.transAxes, fontsize=12, va='top')
fig.suptitle('구매 전환 퍼널 | 2015-09-15 21:00 UTC 기준', fontsize=15, y=0.96)
fig.text(0.02, 0.02, '방문자별 timestamp 순서로 View → AddToCart → Transaction을 모두 충족할 때 다음 단계로 인정. 세션/최대 경과시간 제한 없음; 같은 밀리초 이벤트 간 순서는 인정하지 않음.', fontsize=8, color='#555')
fig.subplots_adjust(left=0.12, right=0.98, top=0.86, bottom=0.15, wspace=0.16)
FIGURE.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(FIGURE, dpi=180, bbox_inches='tight')

print(f'View unique visitors: {counts[0]:,}')
print(f'AddToCart after View: {counts[1]:,} ({step_rates[1]:.2%} of View)')
print(f'Transaction after AddToCart: {counts[2]:,} ({step_rates[2]:.2%} of AddToCart)')
print(f'Overall View to Transaction: {overall_rate:.2%}')
print(f'Figure: {FIGURE}')
print(f'Summary: {SUMMARY}')

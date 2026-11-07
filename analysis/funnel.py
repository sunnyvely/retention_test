import csv
from collections import defaultdict
from pathlib import Path
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1]
source = root / 'data' / 'raw' / 'events.csv'
out = root / 'outputs' / 'figures' / 'funnel.png'
# One chronological sequence per visitor; repeated events count once per stage.
events = defaultdict(list)
with source.open(newline='', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        if row['event'] in {'view', 'addtocart', 'transaction'}:
            events[row['visitorid']].append((int(row['timestamp']), row['event']))

counts = [0, 0, 0]
for seq in events.values():
    seq.sort(key=lambda x: x[0])
    viewed = False
    carted = False
    purchased = False
    view_t = cart_t = None
    for t, event in seq:
        if event == 'view' and not viewed:
            viewed, view_t = True, t
        elif event == 'addtocart' and viewed and not carted and t > view_t:
            carted, cart_t = True, t
        elif event == 'transaction' and carted and not purchased and t > cart_t:
            purchased = True
    if viewed:
        counts[0] += 1
    if carted:
        counts[1] += 1
    if purchased:
        counts[2] += 1

stage = ['View', 'Add to cart', 'Purchase']
step_rates = [None, counts[1]/counts[0], counts[2]/counts[1]]
overall_rates = [1, counts[1]/counts[0], counts[2]/counts[0]]
plt.rcParams['font.family'] = ['Malgun Gothic', 'DejaVu Sans']
fig, ax = plt.subplots(figsize=(9.5, 5.5))
colors = ['#4C78A8', '#F2A541', '#54A24B']
y = range(3)
ax.barh(list(y), counts, color=colors, height=.58)
ax.set_yticks(list(y), stage)
ax.invert_yaxis()
ax.set_xlabel('Unique visitors')
ax.set_title('Purchase funnel | 2015-05-03 to 2015-09-18 (UTC)')
ax.grid(axis='x', alpha=.2)
ax.set_axisbelow(True)
for i, n in enumerate(counts):
    ax.text(n + max(counts)*.012, i, f'{n:,}  ({overall_rates[i]:.1%} of view)' + (f'  |  step CVR {step_rates[i]:.1%}' if i else ''), va='center', fontsize=10)
ax.set_xlim(0, max(counts)*1.48)
fig.text(.01, .01, 'Visitor-level sequential funnel; repeated events counted once. Step rates: cart/view and purchase/cart.', fontsize=8, color='#555')
fig.tight_layout(rect=(0, .04, 1, 1))
out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(out, dpi=180, bbox_inches='tight')
print('Counts (view, cart, purchase):', counts)
print('Step conversion cart/view:', f'{step_rates[1]:.6%}')
print('Step conversion purchase/cart:', f'{step_rates[2]:.6%}')
print('Overall purchase/view:', f'{overall_rates[2]:.6%}')
print('Output:', out)

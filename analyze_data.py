import json

data = json.load(open('MAD_full_dataset.json', 'r', encoding='utf-8'))
print(f'Total entries: {len(data)}')

mas_names = set(d['mas_name'] for d in data)
print(f'MAS systems: {mas_names}')

print(f'Keys per entry: {list(data[0].keys())}')
print(f'Trace keys: {list(data[0]["trace"].keys())}')

benchmarks = set(d['benchmark_name'] for d in data)
print(f'Benchmarks: {benchmarks}')

llms = set(d['llm_name'] for d in data)
print(f'LLMs: {llms}')

# Look at trace structure more carefully
for i, d in enumerate(data[:5]):
    print(f'\n--- Entry {i} ---')
    print(f'  mas_name: {d["mas_name"]}')
    print(f'  trace_id: {d["trace_id"]}')
    t = d['trace']
    print(f'  trace.key: {t["key"]}')
    print(f'  trace.index: {t["index"]}')
    traj = t['trajectory']
    print(f'  trajectory length: {len(traj)}')
    print(f'  trajectory preview: {traj[:500]}')

# Check a few different MAS systems
for mas in mas_names:
    sample = [d for d in data if d['mas_name'] == mas]
    print(f'\n=== {mas} ({len(sample)} entries) ===')
    s = sample[0]
    traj = s['trace']['trajectory']
    print(f'  Trajectory first 800 chars:\n{traj[:800]}')

import json, re

data = json.load(open('MAD_full_dataset.json', 'r', encoding='utf-8'))
ag2 = [d for d in data if d['mas_name'] == 'AG2']

# Check various benchmarks for AG2
benchmarks = set(d['benchmark_name'] for d in ag2)
print(f"AG2 benchmarks: {benchmarks}")

for bench in benchmarks:
    samples = [d for d in ag2 if d['benchmark_name'] == bench]
    s = samples[0]
    traj = s['trace']['trajectory']
    print(f'\n=== AG2 / {bench} (first 2500 chars) ===')
    print(traj[:2500])
    
    # Try various patterns
    names = re.findall(r'name:\s*(\w+)', traj)
    roles = re.findall(r'role:\s*(\w+)', traj)
    to_patterns = re.findall(r'(\w[\w_]*)\s*\(to\s+(\w[\w_]*)\)', traj)
    print(f'  names: {set(names[:20])}')
    print(f'  roles: {set(roles[:20])}')
    print(f'  to_patterns: {to_patterns[:10]}')
    print('---')

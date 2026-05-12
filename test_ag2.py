import json, re, sys

data = json.load(open('MAD_full_dataset.json', 'r', encoding='utf-8'))

# Fix AG2 parser to handle all formats
def parse_ag2_v2(traj):
    agents = {}
    edges = set()
    sequence = []
    
    # Format 1: {'content': ..., 'role': ..., 'name': ...} blocks  
    dict_names = re.findall(r"'name':\s*'(\w[\w_]*)'", traj)
    
    # Format 2: YAML-like role:/name: 
    yaml_names = re.findall(r'(?:^|\n)\s*name:\s*(\w[\w_]*)', traj)
    
    # Format 3: "agent_name (to other_agent):"
    to_patterns = re.findall(r'(\w[\w_ ]*?)\s*\(to\s+(\w[\w_ ]*?)\)', traj)
    
    all_names = list(dict_names) + list(yaml_names)
    
    if all_names:
        # Build agent list
        for name in set(all_names):
            agents.setdefault(name, set())
            nl = name.lower()
            if 'code' in nl or 'executor' in nl or 'proxy' in nl:
                agents[name].add('Code Execution')
            elif 'verif' in nl:
                agents[name].add('Verification')
            elif 'solver' in nl or 'problem' in nl:
                agents[name].add('Problem Solving')
            elif 'manager' in nl or 'chat_manager' in nl:
                agents[name].add('Orchestration')
            elif 'assistant' in nl:
                agents[name].add('LLM Reasoning')
            else:
                agents[name].add('Task Processing')
        
        # Build sequence from order of appearance
        prev = None
        for name in all_names:
            if prev and prev != name:
                edges.add((prev, name))
                sequence.append({'from': prev, 'to': name, 'label': 'message', 'type': 'conversation'})
            prev = name
    
    if to_patterns:
        for frm, to in to_patterns:
            frm, to = frm.strip(), to.strip()
            agents.setdefault(frm, set()).add('Communication')
            agents.setdefault(to, set()).add('Communication')
            edges.add((frm, to))
            sequence.append({'from': frm, 'to': to, 'label': 'message', 'type': 'conversation'})
    
    if not agents:
        agents['Unknown Agent'] = ['Processing']
    
    return {
        'agents': {k: list(v) for k, v in agents.items()},
        'edges': [{'from': e[0], 'to': e[1]} for e in edges],
        'sequence': sequence[:200]
    }

# Test on all AG2 benchmarks
ag2 = [d for d in data if d['mas_name'] == 'AG2']
benchmarks = set(d['benchmark_name'] for d in ag2)
for bench in sorted(benchmarks):
    samples = [d for d in ag2 if d['benchmark_name'] == bench]
    s = samples[0]
    result = parse_ag2_v2(s['trace']['trajectory'])
    print(f'{bench}: agents={list(result["agents"].keys())}, edges={len(result["edges"])}, seq={len(result["sequence"])}')
    
# Count how many have agents now
total_with = 0
total = 0
for d in ag2:
    r = parse_ag2_v2(d['trace']['trajectory'])
    if len(r['agents']) > 1 or (len(r['agents']) == 1 and 'Unknown Agent' not in r['agents']):
        total_with += 1
    total += 1
print(f'\nAG2 with agents: {total_with}/{total}')

"""
Preprocess the MAD dataset to extract structured agent communication data.
Outputs processed_traces.json for the web visualization.

NOTE: This dataset does NOT contain explicit tool information.
We only extract: agent names, communication edges, and call sequences.
"""
import json
import re
import sys

def parse_chatdev(traj):
    agents = set()
    edges = set()
    sequence = []
    
    role_plays = re.findall(r'(\w[\w ]+?)<->(\w[\w ]+?) on : (\w+), turn (\d+)', traj)
    
    seen = set()
    for a1, a2, phase, turn in role_plays:
        a1, a2 = a1.strip(), a2.strip()
        agents.add(a1)
        agents.add(a2)
        edges.add((a1, a2))
        edges.add((a2, a1))
        key = (a1, a2, phase, turn)
        if key not in seen:
            seen.add(key)
            sequence.append({'from': a1, 'to': a2, 'label': f'{phase} (turn {turn})', 'type': 'role_play'})
    
    return {'agents': list(agents), 'edges': [{'from': e[0], 'to': e[1]} for e in edges], 'sequence': sequence}


def parse_metagpt(traj):
    agents = set()
    edges = set()
    sequence = []
    
    # Parse FROM/TO
    comms = re.findall(r'\[.*?\]\s*FROM:\s*([\w\s]+?)\s+TO:\s*(.*?)(?:\n|$)', traj)
    actions = re.findall(r'ACTION:\s*([\w\.]+)', traj)
    
    for i, (frm, to) in enumerate(comms):
        frm = frm.strip()
        to_clean = to.strip().strip("{}'\"")
        if to_clean == '<all>' or not to_clean:
            to_clean = 'All Agents'
        agents.add(frm)
        agents.add(to_clean)
        edges.add((frm, to_clean))
        action = actions[i].split('.')[-1] if i < len(actions) else ''
        sequence.append({'from': frm, 'to': to_clean, 'label': action, 'type': 'message'})
    
    # Parse NEW MESSAGES blocks
    new_msg_blocks = re.findall(r'NEW MESSAGES:\s*\n\s*(\w[\w\s]*?):\s*\n', traj)
    prev_agent = None
    for agent_name in new_msg_blocks:
        agent_name = agent_name.strip()
        agents.add(agent_name)
        if prev_agent and prev_agent != agent_name:
            edges.add((prev_agent, agent_name))
            sequence.append({'from': prev_agent, 'to': agent_name, 'label': 'handoff', 'type': 'message'})
        prev_agent = agent_name
    
    return {'agents': list(agents), 'edges': [{'from': e[0], 'to': e[1]} for e in edges], 'sequence': sequence}


def parse_ag2(traj):
    agents = set()
    edges = set()
    sequence = []
    
    # Format 1: {'content': ..., 'name': 'agent'} dict blocks (MMLU, Olympiad)
    dict_names = re.findall(r"'name':\s*'(\w[\w_]*)'", traj)
    
    # Format 2: YAML-like role:/name: (GSM)
    yaml_names = re.findall(r'(?:^|\n)\s*name:\s*(\w[\w_]*)', traj)
    
    # Format 3: "agent_name (to other_agent):"
    autogen_msgs = re.findall(r'(\w[\w_ ]*?)\s*\(to\s+(\w[\w_ ]*?)\)', traj)
    
    all_names = list(dict_names) + list(yaml_names)
    
    if all_names:
        agents.update(set(all_names))
        prev = None
        for name in all_names:
            if prev and prev != name:
                edges.add((prev, name))
                sequence.append({'from': prev, 'to': name, 'label': 'message', 'type': 'conversation'})
            prev = name
    
    if autogen_msgs:
        for frm, to in autogen_msgs:
            frm, to = frm.strip(), to.strip()
            agents.add(frm)
            agents.add(to)
            edges.add((frm, to))
            sequence.append({'from': frm, 'to': to, 'label': 'message', 'type': 'conversation'})
    
    if not agents:
        agents.add('Unknown Agent')
    
    return {'agents': list(agents), 'edges': [{'from': e[0], 'to': e[1]} for e in edges], 'sequence': sequence}


def parse_magentic(traj):
    agents = set()
    edges = set()
    sequence = []
    
    agent_msgs = re.findall(r'-{10}\s+([\w\s]+?)\s+-{10}', traj)
    
    for msg_agent in agent_msgs:
        agents.add(msg_agent.strip())
    
    prev = None
    for msg_agent in agent_msgs:
        msg_agent = msg_agent.strip()
        if prev and prev != msg_agent:
            edges.add((prev, msg_agent))
            sequence.append({'from': prev, 'to': msg_agent, 'label': 'message', 'type': 'message'})
        prev = msg_agent
    
    return {'agents': list(agents), 'edges': [{'from': e[0], 'to': e[1]} for e in edges], 'sequence': sequence}


def parse_hyperagent(traj):
    agents = set()
    edges = set()
    sequence = []
    
    agent_pats = re.findall(r'(Planner|Navigator|Editor|Executor|Assistant)\b', traj, re.IGNORECASE)
    
    for a in agent_pats:
        agents.add(a.strip().title())
    
    prev = None
    for a in agent_pats:
        a = a.strip().title()
        if prev and prev != a:
            edges.add((prev, a))
            sequence.append({'from': prev, 'to': a, 'label': 'delegation', 'type': 'delegation'})
        prev = a
    
    return {'agents': list(agents), 'edges': [{'from': e[0], 'to': e[1]} for e in edges], 'sequence': sequence}


def parse_appworld(traj):
    agents = set()
    edges = set()
    sequence = []
    
    agents.add('Supervisor')
    
    send_msgs = re.findall(r"send_message\(app_name='(\w+)'.*?message='(.*?)'", traj, re.DOTALL)
    responses = re.findall(r'Response from (\w+) Agent', traj)
    
    for app, msg in send_msgs:
        agents.add(app)
        edges.add(('Supervisor', app))
        sequence.append({'from': 'Supervisor', 'to': app, 'label': msg[:60], 'type': 'api_call'})
    
    for resp in responses:
        agents.add(resp)
        edges.add((resp, 'Supervisor'))
        sequence.append({'from': resp, 'to': 'Supervisor', 'label': 'response', 'type': 'response'})
    
    return {'agents': list(agents), 'edges': [{'from': e[0], 'to': e[1]} for e in edges], 'sequence': sequence}


def parse_openmanus(traj):
    agents = set()
    edges = set()
    sequence = []
    
    agent_names = set(re.findall(r"(\w+)'s thoughts", traj))
    tool_activations = re.findall(r"Activating tool: '([\w_]+)'", traj)
    
    main_agent = list(agent_names)[0] if agent_names else 'Manus'
    agents.add(main_agent)
    
    for tool in tool_activations:
        if tool != main_agent:
            agents.add(tool)
            edges.add((main_agent, tool))
            sequence.append({'from': main_agent, 'to': tool, 'label': 'tool_call', 'type': 'tool_call'})
    
    return {'agents': list(agents), 'edges': [{'from': e[0], 'to': e[1]} for e in edges], 'sequence': sequence}


PARSERS = {
    'ChatDev': parse_chatdev,
    'MetaGPT': parse_metagpt,
    'AG2': parse_ag2,
    'Magentic': parse_magentic,
    'HyperAgent': parse_hyperagent,
    'AppWorld': parse_appworld,
    'OpenManus': parse_openmanus,
}


def main():
    print("Loading dataset...", file=sys.stderr)
    with open('MAD_full_dataset.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"Processing {len(data)} traces...", file=sys.stderr)
    
    results = []
    for i, entry in enumerate(data):
        mas = entry['mas_name']
        parser = PARSERS.get(mas)
        
        if parser:
            try:
                parsed = parser(entry['trace']['trajectory'])
            except Exception as e:
                print(f"Error parsing {mas} trace {entry['trace_id']}: {e}", file=sys.stderr)
                parsed = {'agents': [], 'edges': [], 'sequence': []}
        else:
            parsed = {'agents': [], 'edges': [], 'sequence': []}
        
        # Cap sequence length for performance
        seq = parsed['sequence']
        if len(seq) > 200:
            seq = seq[:200]
        
        results.append({
            'id': i,
            'mas_name': mas,
            'llm_name': entry['llm_name'],
            'benchmark_name': entry['benchmark_name'],
            'trace_id': entry['trace_id'],
            'trace_key': entry['trace'].get('key', ''),
            'agents': parsed['agents'],
            'edges': parsed['edges'],
            'sequence': seq
        })
        
        if (i + 1) % 100 == 0:
            print(f"  Processed {i+1}/{len(data)}", file=sys.stderr)
    
    with open('processed_traces.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)
    
    print(f"\nDone! Total: {len(results)} traces", file=sys.stderr)
    for mas in sorted(PARSERS):
        t = [r for r in results if r['mas_name'] == mas]
        if t:
            aa = sum(len(r['agents']) for r in t) / len(t)
            ss = sum(len(r['sequence']) for r in t) / len(t)
            print(f"  {mas}: {len(t)} traces, avg {aa:.1f} agents, avg {ss:.1f} seq steps", file=sys.stderr)


if __name__ == '__main__':
    main()

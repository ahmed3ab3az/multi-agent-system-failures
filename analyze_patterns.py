import json
import re

data = json.load(open('MAD_full_dataset.json', 'r', encoding='utf-8'))

# Let's examine the trajectory patterns for each MAS to understand agent communication
mas_names = set(d['mas_name'] for d in data)

for mas in sorted(mas_names):
    samples = [d for d in data if d['mas_name'] == mas]
    s = samples[0]
    traj = s['trace']['trajectory']
    
    print(f'\n{"="*60}')
    print(f'MAS: {mas} | trace_id: {s["trace_id"]}')
    print(f'{"="*60}')
    
    if mas == 'ChatDev':
        # Find role-playing patterns
        roles = re.findall(r'\*\*(\w[\w\s]+?)\*\*.*?(?:Start Chat|<->)', traj[:10000])
        phases = re.findall(r'phase_name.*?\|\s*(\w+)', traj[:10000])
        role_plays = re.findall(r'(\w[\w ]+?)<->(\w[\w ]+?) on : (\w+)', traj[:20000])
        print(f'  Role-plays found: {role_plays[:10]}')
        agents = set()
        for r in role_plays:
            agents.add(r[0].strip())
            agents.add(r[1].strip())
        print(f'  Agents: {agents}')
        
    elif mas == 'MetaGPT':
        # Find FROM/TO patterns
        comms = re.findall(r'\[.*?\]\s*FROM:\s*(\w[\w\s]*?)\s*TO:\s*(\{.*?\}|\w[\w\s]*)', traj[:10000])
        print(f'  Communications: {comms[:10]}')
        actions = re.findall(r'ACTION:\s*(.*)', traj[:10000])
        print(f'  Actions: {actions[:10]}')
        
    elif mas == 'AG2':
        # Find agent patterns
        lines = traj[:5000].split('\n')
        for line in lines[:50]:
            if line.strip():
                print(f'  {line.strip()[:120]}')
        
    elif mas == 'Magentic':
        lines = traj[:5000].split('\n')
        agent_lines = [l for l in lines if 'agent' in l.lower() or 'assistant' in l.lower() or 'user' in l.lower()]
        print(f'  Agent-related lines: {agent_lines[:10]}')
        
    elif mas == 'HyperAgent':
        lines = traj[:5000].split('\n')
        for line in lines[:50]:
            if line.strip():
                print(f'  {line.strip()[:120]}')
        
    elif mas == 'AppWorld':
        agent_patterns = re.findall(r'((?:Supervisor|spotify|[\w]+)\s*Agent)', traj[:5000])
        send_msgs = re.findall(r"send_message\(app_name='(\w+)'", traj[:5000])
        print(f'  Agent patterns: {agent_patterns[:10]}')
        print(f'  Message sends: {send_msgs[:10]}')
        lines = traj[:3000].split('\n')
        for line in lines[:40]:
            if line.strip():
                print(f'  {line.strip()[:120]}')
    
    elif mas == 'OpenManus':
        lines = traj[:5000].split('\n')
        for line in lines[:40]:
            if line.strip():
                print(f'  {line.strip()[:120]}')

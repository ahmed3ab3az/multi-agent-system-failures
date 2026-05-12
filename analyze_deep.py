import json
import re

data = json.load(open('MAD_full_dataset.json', 'r', encoding='utf-8'))

# Deep dive into each MAS trajectory for agent/tool extraction patterns

# 1. ChatDev - need ALL agent role-plays and phases
chatdev = [d for d in data if d['mas_name'] == 'ChatDev'][0]
traj = chatdev['trace']['trajectory']
role_plays = re.findall(r'(\w[\w ]+?)<->(\w[\w ]+?) on : (\w+)', traj)
all_agents = set()
for r in role_plays:
    all_agents.add(r[0].strip())
    all_agents.add(r[1].strip())
print(f'ChatDev agents: {all_agents}')
print(f'ChatDev phases: {set(r[2] for r in role_plays)}')
print(f'ChatDev role-play pairs: {set((r[0].strip(), r[1].strip()) for r in role_plays)}')

# 2. MetaGPT - Find all FROM/TO patterns
metagpt = [d for d in data if d['mas_name'] == 'MetaGPT'][0]
traj = metagpt['trace']['trajectory']
comms = re.findall(r'\[.*?\]\s*FROM:\s*([\w\s]+?)\s+TO:\s*(.*?)(?:\n|$)', traj)
print(f'\nMetaGPT communications: {comms[:20]}')
agents_meta = set()
for c in comms:
    agents_meta.add(c[0].strip())
    to = c[1].strip().strip("{}'")
    agents_meta.add(to)
print(f'MetaGPT agents: {agents_meta}')

# Also find actions/tools
actions_meta = re.findall(r'ACTION:\s*([\w\.]+)', traj)
print(f'MetaGPT actions: {set(actions_meta)}')

# 3. AG2 - more detailed
ag2 = [d for d in data if d['mas_name'] == 'AG2'][0]
traj = ag2['trace']['trajectory']
print(f'\nAG2 trajectory first 3000 chars:')
print(traj[:3000])

# 4. Magentic - find actual agent communication after install logs
magentic = [d for d in data if d['mas_name'] == 'Magentic'][0]
traj = magentic['trace']['trajectory']
# Skip install logs - look later in trajectory
idx = traj.find('---------- ')
if idx > 0:
    print(f'\nMagentic from agent comms:')
    print(traj[idx:idx+3000])
else:
    # search for different pattern
    for pat in ['Orchestrator', 'Coder', 'WebSurfer', 'FileSurfer', 'Executor']:
        idx = traj.find(pat)
        if idx > 0:
            print(f'\nMagentic found {pat} at {idx}:')
            print(traj[max(0,idx-200):idx+1000])
            break

# 5. HyperAgent deeper
hyper = [d for d in data if d['mas_name'] == 'HyperAgent'][0]
traj = hyper['trace']['trajectory']
# Find agent patterns
agents_hyper = re.findall(r'(Navigator|Executor|Editor|Planner|Assistant)\s*(?:Agent)?', traj[:10000], re.IGNORECASE)
print(f'\nHyperAgent agent mentions: {set(agents_hyper[:30])}')
# Find tool uses
tools_hyper = re.findall(r'Tool:\s*([\w_]+)', traj[:10000])
print(f'HyperAgent tools: {set(tools_hyper[:30])}')
print(f'\nHyperAgent traj[500:3000]:')
print(traj[500:3000])

# 6. OpenManus deeper
openmanus = [d for d in data if d['mas_name'] == 'OpenManus'][0]
traj = openmanus['trace']['trajectory']
tools_om = re.findall(r"Tools being prepared: \[(.*?)\]", traj[:20000])
print(f'\nOpenManus tools: {tools_om[:10]}')
agents_om = re.findall(r"(\w+)'s thoughts", traj[:20000])
print(f'OpenManus agents: {set(agents_om)}')

# 7. AppWorld deeper
appworld = [d for d in data if d['mas_name'] == 'AppWorld'][0]
traj = appworld['trace']['trajectory']
agent_pats = re.findall(r'(?:Response from|Message to|Entering)\s+([\w\s]+?)\s+(?:Agent|agent)', traj[:10000])
print(f'\nAppWorld agents: {set(a.strip() for a in agent_pats)}')
app_names = re.findall(r"send_message\(app_name='(\w+)'", traj[:20000])
print(f'AppWorld app interactions: {set(app_names)}')

import json, re

data = json.load(open('MAD_full_dataset.json', 'r', encoding='utf-8'))

# Deep dive AG2 - look at multiple samples
ag2_samples = [d for d in data if d['mas_name'] == 'AG2']
for i in range(min(5, len(ag2_samples))):
    traj = ag2_samples[i]['trace']['trajectory']
    print(f'\n=== AG2 sample {i}, benchmark={ag2_samples[i]["benchmark_name"]} ===')
    print(traj[:2000])
    print('---')

# MetaGPT - check more thoroughly
meta_samples = [d for d in data if d['mas_name'] == 'MetaGPT']
for i in range(min(3, len(meta_samples))):
    traj = meta_samples[i]['trace']['trajectory']
    print(f'\n=== MetaGPT sample {i} ===')
    print(traj[:3000])
    print('---')

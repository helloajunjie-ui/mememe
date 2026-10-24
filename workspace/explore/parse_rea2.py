import re
p = r'F:/me/self-agent/workspace/explore/rea_20261016.html'
h = open(p, encoding='utf-8', errors='ignore').read()

for kw in ['stargazerCount', 'stargazers', 'forkCount', 'watcherCount', 'starCount', 'socialCount']:
    idxs = [m.start() for m in re.finditer(kw, h)]
    print('==', kw, 'hits:', len(idxs))
    for i in idxs[:4]:
        print('   ...', h[max(0, i-60):i+90].replace('\n', ' '))

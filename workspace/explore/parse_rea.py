import re
p = r'F:/me/self-agent/workspace/explore/rea_20261016.html'
h = open(p, encoding='utf-8', errors='ignore').read()

pats = {
    'stars_counter': r'id="repo-stars-counter-star"[^>]*title="([0-9,]+)"',
    'stars_title_first': r'title="([0-9,]+)"[^>]*id="repo-stars-counter-star"',
    'network_counter': r'id="repo-network-counter"[^>]*title="([0-9,]+)"',
    'network_title_first': r'title="([0-9,]+)"[^>]*id="repo-network-counter"',
    'notif_counter': r'id="repo-notifications-counter"[^>]*title="([0-9,]+)"',
    'aria_star': r'aria-label="([0-9,]+) users? starred this repository"',
    'aria_fork': r'aria-label="([0-9,]+) users? forked this repository"',
    'og_desc': r'<meta name="description" content="([^"]{0,200})',
}
for k, v in pats.items():
    print(k, '=>', re.findall(v, h)[:3])

# 兜底：找所有形如 "123,456" 的 title 属性附近
for m in re.finditer(r'([0-9][0-9,]{3,})\s*(stars|forks|watching)', h):
    print('near:', m.group(0))
    break

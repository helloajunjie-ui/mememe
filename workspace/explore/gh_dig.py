# -*- coding: utf-8 -*-
"""GitHub 深挖取数：拉仓库文件树 + README 到本地（走代理，ASCII 输出）。
用法: python gh_dig.py owner/repo [owner/repo ...]
产出: workspace/explore/dig/<owner>__<repo>.tree.txt 和 .README.md
"""
import json
import os
import sys
import urllib.request

PROXY = 'http://127.0.0.1:7897'
OUTDIR = r'F:\me\self-agent\workspace\explore\dig'


def make_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))


def get_json(op, url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'suyue-explore', 'Accept': 'application/vnd.github+json'})
    with op.open(req, timeout=60) as r:
        return json.load(r)


def get_text(op, url):
    req = urllib.request.Request(url, headers={'User-Agent': 'suyue-explore'})
    with op.open(req, timeout=60) as r:
        return r.read().decode('utf-8', 'replace')


def dig(op, repo):
    name = repo.replace('/', '__')
    meta = get_json(op, 'https://api.github.com/repos/' + repo)
    branch = meta.get('default_branch', 'main')
    tree = get_json(op, 'https://api.github.com/repos/%s/git/trees/%s?recursive=1' % (repo, branch))
    blobs = [t for t in tree.get('tree', []) if t['type'] == 'blob']
    paths = [t['path'] for t in blobs]
    tp = os.path.join(OUTDIR, name + '.tree.txt')
    open(tp, 'w', encoding='utf-8').write('\n'.join(paths))
    print('TREE   %-22s files=%-5d truncated=%s -> %s' % (repo, len(paths), tree.get('truncated'), tp))

    for cand in ['README.md', 'readme.md', 'README.rst', 'README.txt']:
        if cand in paths:
            txt = get_text(op, 'https://raw.githubusercontent.com/%s/%s/%s' % (repo, branch, cand))
            rp = os.path.join(OUTDIR, name + '.README.md')
            open(rp, 'w', encoding='utf-8').write(txt)
            print('README %-22s %s chars=%-7d -> %s' % (repo, cand, len(txt), rp))
            break

    docs = [p for p in paths if p.lower().endswith('.md')]
    print('MD_FILES %s count=%d' % (repo, len(docs)))
    for p in docs[:60]:
        print('   md: ' + p)


def main():
    repos = sys.argv[1:]
    if not repos:
        print('usage: python gh_dig.py owner/repo [...]')
        return 1
    os.makedirs(OUTDIR, exist_ok=True)
    op = make_opener()
    for repo in repos:
        try:
            dig(op, repo)
        except Exception as e:
            print('ERR %s: %s' % (repo, e))
        print('=' * 70)
    return 0


if __name__ == '__main__':
    sys.exit(main())

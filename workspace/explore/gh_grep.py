# -*- coding: utf-8 -*-
"""一步到位：拉 GitHub 单个文件（带本地缓存）+ 正则 grep，结果写 UTF-8 文件。
用法: python gh_grep.py <owner/repo> <branch> <path> <pattern> [limit] [outfile]
缓存命名与 gh_raw.py 一致: dig/<owner>__<repo>__<path 扁平化>
"""
import os
import re
import sys
import urllib.request

PROXY = 'http://127.0.0.1:7897'
OUTDIR = r'F:\me\self-agent\workspace\explore\dig'


def make_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))


def fetch_cached(repo, branch, path):
    flat = path.replace('/', '_')
    dst = os.path.join(OUTDIR, '%s__%s' % (repo.replace('/', '__'), flat))
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst, True
    url = 'https://raw.githubusercontent.com/%s/%s/%s' % (repo, branch, path)
    op = make_opener()
    req = urllib.request.Request(url, headers={'User-Agent': 'suyue-explore'})
    with op.open(req, timeout=60) as r:
        data = r.read()
    os.makedirs(OUTDIR, exist_ok=True)
    open(dst, 'wb').write(data)
    return dst, False


def main():
    if len(sys.argv) < 5:
        print('usage: python gh_grep.py <owner/repo> <branch> <path> <pattern> [limit] [outfile]')
        return 1
    repo, branch, path, pat = sys.argv[1:5]
    limit = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else 40
    outfile = sys.argv[6] if len(sys.argv) > 6 else None

    local, cached = fetch_cached(repo, branch, path)
    size = os.path.getsize(local)
    print('file %s (%d bytes, cached=%s)' % (os.path.basename(local), size, cached))

    rx = re.compile(pat, re.I)
    out = []
    for i, ln in enumerate(open(local, encoding='utf-8', errors='replace'), 1):
        if rx.search(ln):
            out.append('%5d | %s' % (i, ln.rstrip()))
            if len(out) >= limit:
                out.append('... (reach max_lines=%d)' % limit)
                break
    out.append('== matched %d (limit %d) in %s' % (len(out), limit, path))
    text = '\n'.join(out)
    if outfile:
        open(outfile, 'w', encoding='utf-8').write(text)
        print('wrote %d lines -> %s' % (len(out), outfile))
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())

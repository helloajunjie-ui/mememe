# -*- coding: utf-8 -*-
"""从 GitHub 拉单个原始文件到本地 dig/ 目录（走代理，ASCII 输出）。
用法: python gh_raw.py <owner/repo> <branch> <path> [path ...]
产出: workspace/explore/dig/<owner>__<repo>__<flatten_path>
"""
import os
import sys
import urllib.request

PROXY = 'http://127.0.0.1:7897'
OUTDIR = r'F:\me\self-agent\workspace\explore\dig'


def make_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))


def main():
    if len(sys.argv) < 4:
        print('usage: python gh_raw.py <owner/repo> <branch> <path> [path ...]')
        return 1
    repo, branch = sys.argv[1], sys.argv[2]
    paths = sys.argv[3:]
    os.makedirs(OUTDIR, exist_ok=True)
    op = make_opener()
    prefix = repo.replace('/', '__')
    for p in paths:
        url = 'https://raw.githubusercontent.com/%s/%s/%s' % (repo, branch, p)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'suyue-explore'})
            with op.open(req, timeout=60) as r:
                data = r.read()
            flat = p.replace('/', '_')
            dst = os.path.join(OUTDIR, '%s__%s' % (prefix, flat))
            open(dst, 'wb').write(data)
            print('OK  %-50s %8d bytes -> %s' % (p, len(data), os.path.basename(dst)))
        except Exception as e:
            print('ERR %-50s %s' % (p, e))
    return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""GitHub 仓库体征探针（走本地代理，纯 ASCII 输出，避免 GBK 控制台问题）。
用法: python gh_probe.py owner/repo [owner/repo ...]
"""
import json
import sys
import urllib.request

PROXY = 'http://127.0.0.1:7897'


def make_opener():
    ph = urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY})
    return urllib.request.build_opener(ph)


def get(opener, url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'suyue-explore',
        'Accept': 'application/vnd.github+json',
    })
    with opener.open(req, timeout=40) as r:
        return json.load(r)


def main():
    repos = sys.argv[1:]
    if not repos:
        print('usage: python gh_probe.py owner/repo [...]')
        return 1
    op = make_opener()
    for repo in repos:
        try:
            d = get(op, 'https://api.github.com/repos/' + repo)
            lic = (d.get('license') or {}).get('spdx_id') or '-'
            print('%-24s stars=%-7s forks=%-6s lang=%-12s issues=%-5s size=%-8sKB' % (
                repo, d.get('stargazers_count'), d.get('forks_count'),
                d.get('language'), d.get('open_issues_count'), d.get('size')))
            print('%-24s created=%s  pushed=%s  license=%s' % (
                '', d.get('created_at', '')[:10], d.get('pushed_at', '')[:10], lic))
            print('%-24s desc=%s' % ('', (d.get('description') or '')[:150]))
            print('%-24s topics=%s' % ('', ','.join(d.get('topics') or [])))
            print('-' * 70)
        except Exception as e:
            print('%-24s ERROR %s' % (repo, e))
    return 0


if __name__ == '__main__':
    sys.exit(main())

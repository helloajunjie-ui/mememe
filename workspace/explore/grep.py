# -*- coding: utf-8 -*-
"""对（文件树/文本）按正则做行级 grep，输出匹配行 + 行号。ASCII 安全。
用法: python grep.py <file> <pattern> [max_lines] [outfile]
outfile 存在时写入 UTF-8 文件（避免 PowerShell > 重定向写成 UTF-16）。
"""
import re
import sys


def main():
    if len(sys.argv) < 3:
        print('usage: python grep.py <file> <pattern> [max_lines] [outfile]')
        return 1
    path, pat = sys.argv[1], sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else 40
    outfile = sys.argv[4] if len(sys.argv) > 4 else None

    rx = re.compile(pat, re.I)
    out = []
    for i, ln in enumerate(open(path, encoding='utf-8', errors='replace'), 1):
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

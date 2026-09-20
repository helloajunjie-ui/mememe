# -*- coding: utf-8 -*-
"""按行号区间切文件（带行号），写 UTF-8。避免 fs_read 截断 + PowerShell 重定向编码坑。
用法: python lines.py <file> <start> <end> [outfile]
"""
import sys


def main():
    if len(sys.argv) < 4:
        print('usage: python lines.py <file> <start> <end> [outfile]')
        return 1
    path = sys.argv[1]
    start, end = int(sys.argv[2]), int(sys.argv[3])
    outfile = sys.argv[4] if len(sys.argv) > 4 else None

    lines = open(path, encoding='utf-8', errors='replace').read().splitlines()
    out = ['%5d | %s' % (i, lines[i - 1]) for i in range(start, min(end, len(lines)) + 1)]
    text = '\n'.join(out)
    if outfile:
        open(outfile, 'w', encoding='utf-8').write(text)
        print('wrote %d lines (%d-%d) -> %s' % (len(out), start, end, outfile))
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())

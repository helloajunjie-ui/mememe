# -*- coding: utf-8 -*-
"""UTF-8 安全追加：把 source 文本追加到 target 末尾。
用法: python append.py <target> <source>
避免 PowerShell '>>' 写成 UTF-16。
"""
import os
import sys


def main():
    if len(sys.argv) < 3:
        print('usage: python append.py <target> <source>')
        return 1
    target, source = sys.argv[1], sys.argv[2]
    text = open(source, encoding='utf-8').read()
    with open(target, 'a', encoding='utf-8', newline='') as f:
        f.write('\n\n' + text.rstrip() + '\n')
    print('appended %d chars -> %s (now %d bytes)' % (len(text), target, os.path.getsize(target)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

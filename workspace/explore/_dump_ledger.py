# -*- coding: utf-8 -*-
"""量一下 ledger.md 的真实长度与尾部内容，判断是否有回写的截断标记。"""
import os

P = r'F:\me\self-agent\workspace\explore\ledger.md'
OUT = r'F:\me\self-agent\workspace\explore\_tail.txt'

raw = open(P, 'rb').read()
c = raw.decode('utf-8')

lines = []
lines.append('FILE_BYTES=%d' % len(raw))
lines.append('FILE_CHARS=%d' % len(c))
lines.append('HAS_TRUNC_MARK=%s' % ('已截断' in c))
lines.append('COUNT_TRUNC_MARK=%d' % c.count('已截断'))
# 所有小标题（## / ### 开头）的行号与内容
lines.append('---HEADINGS---')
for i, ln in enumerate(c.splitlines(), 1):
    if ln.startswith('#'):
        lines.append('L%d: %s' % (i, ln))
lines.append('---CHARS_FROM_4000---')
lines.append(c[4000:])

open(OUT, 'w', encoding='utf-8').write('\n'.join(lines))
print('OK bytes=%d chars=%d' % (len(raw), len(c)))

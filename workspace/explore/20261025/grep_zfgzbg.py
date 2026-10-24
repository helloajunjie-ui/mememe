# -*- coding: utf-8 -*-
"""抓 2026 年政府工作报告全文，定位所有含"养老金/退休/医保"的句子"""
import re, sys
import requests

url = "http://www.71.cn/2026/0313/1286460.shtml"
r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
r.encoding = r.apparent_encoding if r.encoding in (None, 'ISO-8859-1') else r.encoding
html = r.text
# 去脚本/样式，再去标签
html = re.sub(r'(?is)<(script|style).*?</\1>', '', html)
text = re.sub(r'(?s)<[^>]+>', '', html)
text = re.sub(r'[ \t\u3000]+', ' ', text)
text = re.sub(r'\n{2,}', '\n', text)

print("HTTP", r.status_code, "chars", len(text))

# 关键断言核对
for kw in ["上调退休人员基本养老金", "退休人员基本养老金", "退休人员", "养老金", "基础养老金"]:
    hits = [m.start() for m in re.finditer(kw, text)]
    print(f"\n=== {kw} : {len(hits)} 处 ===")
    for p in hits[:12]:
        s = text[max(0, p-70):p+80].replace("\n", " ")
        print("   ...", s, "...")

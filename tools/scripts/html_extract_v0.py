# -*- coding: utf-8 -*-
"""M2 采集原型 v0：把 HTML 按标题分节，段内行内标签只保留文字。
目的：验证"自己能做出不丢正文的提取"，并和 net_fetch 的输出对照。
"""
import re
import sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DROP_BLOCKS = re.compile(
    r"<(script|style|nav|header|footer|svg|noscript)\b.*?</\1>",
    re.S | re.I,
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def strip_inline(html: str) -> str:
    """去掉所有标签但保留文字；块级标签转换行。"""
    s = DROP_BLOCKS.sub(" ", html)
    # 块级标签 -> 换行
    s = re.sub(r"</?(p|div|li|ul|ol|h[1-6]|tr|table|pre|blockquote)\b[^>]*>", "\n", s, flags=re.I)
    # 其余标签直接去掉（保留其中文字）
    s = re.sub(r"<[^>]+>", "", s)
    # 实体
    for a, b in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("¶", "")]:
        s = s.replace(a, b)
    # 空白归一
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def split_by_heading(text: str) -> list:
    """按 markdown 风格的行 (# 标题) 切分节。"""
    out, cur_title, buf = [], None, []
    for line in text.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if re.match(r"^#{1,6}\s", line):
            if cur_title is not None:
                out.append((cur_title, "\n".join(buf).strip()))
            cur_title, buf = line, []
        else:
            buf.append(line)
    if cur_title is not None:
        out.append((cur_title, "\n".join(buf).strip()))
    return out


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://docs.vllm.ai/en/latest/usage/troubleshooting.html"
    html = fetch(url)
    print(f"[URL] {url}")
    print(f"[HTML] {len(html)} 字符")

    # 只取主内容区（mkdocs: <article class="md-content__inner ...">）
    m = re.search(r'<article[^>]*>(.*?)</article>', html, re.S | re.I)
    body = m.group(1) if m else html
    print(f"[正文区] {len(body)} 字符{'（已裁剪到 article）' if m else '（未找到 article，用整页）'}")

    txt = strip_inline(body)
    # 给 h1~h6 加 markdown 标记，便于分节与阅读
    txt = re.sub(r"^\s*(Hangs downloading a model|Hangs loading a model from disk|"
                 r"Out of memory|Generation quality changed|Enable more logging|"
                 r"Breakpoints|Incorrect network setup|Error near self\.graph\.replay\(\))\s*$",
                 r"## \1", txt, flags=re.M)

    secs = split_by_heading(txt)
    print(f"[分节] {len(secs)} 节")
    for title, bodytext in secs[:6]:
        print("=" * 60)
        print(title)
        print("-" * 60)
        print(bodytext[:600])
    print("=" * 60)

    with open("extract_out.txt", "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"[落盘] extract_out.txt，共 {len(txt)} 字符")


if __name__ == "__main__":
    main()

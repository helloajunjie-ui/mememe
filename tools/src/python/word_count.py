import re
from collections import Counter
from tools.base import tool

@tool(
    'word_count',
    '统计一段文本：字符数(不含空白)、单词数、行数、出现频率最高的前N个单词及其频次',
    {
        'text': {'type': 'string', 'description': '要统计的文本字符串', 'required': True},
        'top_n': {'type': 'integer', 'description': '返回最高频单词个数，默认3', 'required': False}
    }
)
def run(text: str, top_n: int = 3) -> dict:
    if text is None:
        text = ''

    # 字符数（不含空白：空格/换行/制表等）
    chars_no_ws = len(re.sub(r'\s', '', text))

    # 行数
    lines = text.splitlines()
    line_count = len(lines)
    # 空文本算 0 行；纯空行文本按空行数算
    if text == '':
        line_count = 0

    # 单词：连续非空白片段 + 中文字符按字切分，统一小写
    # 先按空白切分出 tokens
    raw_tokens = text.split()
    words = []
    for tok in raw_tokens:
        # 英文单词（含连字符/撇号）
        for w in re.findall(r"[A-Za-z]+(?:['-][A-Za-z]+)*", tok):
            words.append(w.lower())
        # 中文字符（连续汉字）
        for c in re.findall(r'[\u4e00-\u9fff]+', tok):
            # 连续汉字作为一个词
            words.append(c)
        # 其他（数字等）暂不计数

    word_count = len(words)
    freq = Counter(words)
    top = freq.most_common(top_n)

    return {
        'chars_no_whitespace': chars_no_ws,
        'word_count': word_count,
        'line_count': line_count,
        f'top_{top_n}_words': [{'word': w, 'count': c} for w, c in top]
    }

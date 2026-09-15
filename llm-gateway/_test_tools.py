# -*- coding: utf-8 -*-
"""验证带 tools 的调用 → tool_calls 格式透传（对齐素月原有 {id,name,arguments}）"""
import sys, json, urllib.request
sys.path.insert(0, r"F:\me\self-agent")
from core.llm import LLMGateway

llm = LLMGateway(base_url="https://api.yuegle.com", api_key="sk-test",
                 model="gemini-2.5-flash", max_tokens=8192, timeout=60)
tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询城市天气",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    }
}]
r = llm.chat([{"role": "user", "content": "查一下北京的天气"}], tools=tools, tool_choice="auto")
print("content:", str(r.get("content"))[:30])
print("tool_calls:", json.dumps(r.get("tool_calls"), ensure_ascii=False)[:200])
print("finish_reason:", r.get("finish_reason"), "| model:", r.get("model"))
assert all(k in (r.get("tool_calls") or [{}])[0] for k in ("id", "name", "arguments")), "tool_calls 键缺失"
print("PASS")

# -*- coding: utf-8 -*-
"""白绫 LLMGateway（网关客户端）回归测试：正常对话 / 自动拉起 / 容灾透传"""
import json, os, sys, time, urllib.request

sys.path.insert(0, r"F:\me\self-agent")
from core.llm import LLMGateway

def chat_once(tag):
    llm = LLMGateway(base_url="https://api.yuegle.com", api_key="sk-test",
                     model="gemini-2.5-flash", max_tokens=8192, timeout=60)
    r = llm.chat([{"role": "user", "content": "回一句话，测试" + tag}], temperature=0.7)
    return r, llm

# 1) 正常对话（要求网关在线）
r, llm = chat_once("A")
print("1) 对话:", "OK" if r.get("content") else f"FAIL {r.get('error')}",
      "| 回复:", str(r.get("content"))[:36],
      "| 模型:", r.get("model"),
      "| failover:", r.get("failover_note"))

# 2) 工具调用格式（透传检查）
r, _ = chat_once("B")
print("2) tool_calls 键:", "OK" if all(k in (r.get("tool_calls") or [{}])[0] for k in ("id", "name", "arguments")) else "N/A")

# 3) 自动拉起：停掉网关 → chat → 应自动拉起并成功
os.system("powershell -NoProfile -Command \"Get-Process bailing-gateway -ErrorAction SilentlyContinue | Stop-Process -Force\"")
time.sleep(1)
print("   已停网关，等待自动拉起...")
t0 = time.time()
r, llm = chat_once("C")
print("3) 自动拉起:", "OK" if r.get("content") else f"FAIL {r.get('error')}",
      "| 回复:", str(r.get("content"))[:36], "| 耗时 %.0fs" % (time.time() - t0))

# 4) 容灾透传：改真实配置锚点为坏模型 → chat → 网关容灾 → 白绫拿到 failover_note
CFG = r"F:\me\self-agent\config\llm.json"
d = json.load(open(CFG, encoding="utf-8"))
backup = dict(d)
d["model"] = "gemini-2.5-flash-lite"          # 健康表 bad
d["preferred_model"] = "gemini-2.5-flash-lite"
json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
time.sleep(1.5)  # 等网关热重载
r, llm = chat_once("D")
print("4) 容灾透传:", "OK" if r.get("content") else f"FAIL {r.get('error')}",
      "| 模型:", r.get("model"),
      "| failover_note:", str(r.get("failover_note"))[:56])
# 恢复配置
json.dump(backup, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("   已恢复真实配置")

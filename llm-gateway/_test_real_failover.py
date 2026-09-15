# -*- coding: utf-8 -*-
"""Go 网关：真容灾（锚点模型变坏 → 容灾切可用）"""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8766"
CFG = r"F:\me\self-agent\llm-gateway\_testcfg\llm.json"

def post(path, body=None, timeout=240):
    data = json.dumps(body if body is not None else {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}

def get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode())

def chat(msg):
    return post("/v1/chat", {"messages": [{"role": "user", "content": msg}], "temperature": 0.7})

def set_bad_anchor():
    """锚点 = 当前 = 坏模型（gemini-2.5-flash-lite，健康表 bad）：回锚无目标，只能容灾"""
    d = json.load(open(CFG, encoding="utf-8"))
    d["model"] = "gemini-2.5-flash-lite"
    d["preferred_model"] = "gemini-2.5-flash-lite"
    d["preferred_base_url"] = "https://api.yuegle.com"
    json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

set_bad_anchor()
time.sleep(1.5)
r = chat("真容灾测试：请回一句话")
print("容灾 chat:", "OK" if r.get("content") else "FAIL",
      "| 回复:", str(r.get("content"))[:40],
      "| 使用模型:", r.get("model"),
      "| failover_note:", str(r.get("failover_note"))[:80])
c = get("/api/config")
print("当前:", c.get("model"), "| 锚点:", c.get("preferred_model"), "| failover:", c.get("failover"))

# 恢复现场
d = json.load(open(CFG, encoding="utf-8"))
d["model"] = "gemini-2.5-flash"
d["preferred_model"] = "gemini-2.5-flash"
json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("已恢复锚点模型")

# -*- coding: utf-8 -*-
"""Go 网关：热重载 + 真容灾回归（改文件→chat→自动切）"""
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

def set_model(model):
    d = json.load(open(CFG, encoding="utf-8"))
    d["model"] = model
    json.dump(d, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# 1) 基础对话（当前应 deepseek-v4-pro，上次测试遗留）
c = get("/api/config")
print("0) 当前:", c.get("base_url"), "/", c.get("model"))

# 2) 热重载：改文件 model=坏模型 → chat 应 reload + 503 + 容灾切换成功
set_model("gemini-3-pro-preview")
time.sleep(1.5)
r = chat("容灾热重载测试：请简短回复")
print("1) 热重载+容灾 chat:", "OK" if r.get("content") else "FAIL",
      "| 回复:", str(r.get("content"))[:40],
      "| 使用模型:", r.get("model"),
      "| failover:", str(r.get("failover_note"))[:70])
c = get("/api/config")
print("   当前已切至:", c.get("model"))

# 3) 再次改坏模型 → 第二轮容灾（冷却窗口内应仍能切换）
set_model("gemini-3-pro-preview")
time.sleep(1.5)
r = chat("第二轮容灾测试：请简短回复")
print("2) 第二轮容灾:", "OK" if r.get("content") else "FAIL", "| 回复:", str(r.get("content"))[:40],
      "| 模型:", r.get("model"))

# 4) 恢复锚点模型
set_model("gemini-2.5-flash")
time.sleep(1.5)
c = get("/api/config")
print("3) 恢复:", c.get("model"), "| 锚点:", c.get("preferred_model"))

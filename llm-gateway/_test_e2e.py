# -*- coding: utf-8 -*-
"""Go 网关端到端测试：health/config/scan/chat/switch/容灾。"""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8766"

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

# 0) 探活 + 配置读取（兼容现有格式）
print("0) /health:", get("/health").get("ok"))
c = get("/api/config")
print("   配置:", c.get("base_url"), "/", c.get("model"), "| key:", c.get("api_key_masked"),
      "| failover:", c.get("failover"), "| 锚点:", c.get("preferred_model"))
m = get("/api/models")
print("   models:", len(m.get("models", [])), "| usable:", m.get("usable"), "| bad 数:", len(m.get("bad", [])))

# 1) 健康扫描（并发）
t0 = time.time()
r = post("/api/llm/scan")
print("1) scan ok:", r.get("ok"), "耗时 %.0fs" % (time.time()-t0))
for k, v in (r.get("summary") or {}).items():
    print("   ", k, "->", v.get("ok"), "/", v.get("total"), (v.get("ok_models") or [])[:3])

# 2) 对话代理（月歌 gemini-2.5-flash）
t0 = time.time()
r = chat("你好，回一句话即可")
print("2) chat:", "OK" if r.get("content") else "FAIL",
      "| 回复:", str(r.get("content"))[:40], "| 模型:", r.get("model"), "| %.1fs" % (time.time()-t0))

# 3) 渠道切换（DEEPSEEK）→ 对话
r = post("/api/llm/switch", {"source_name": "DEEPSEEK", "model": "deepseek-v4-pro"})
print("3) 切 DEEPSEEK:", json.dumps({k: r.get(k) for k in ("ok", "base_url", "model", "error")}, ensure_ascii=False))
r = chat("再回一句话")
print("   切换后 chat:", "OK" if r.get("content") else "FAIL", "| 回复:", str(r.get("content"))[:40])

# 4) 容灾：把模型改成坏模型 → chat 应自动切换成功
d = json.load(open(r"F:\me\self-agent\llm-gateway\_testcfg\llm.json", encoding="utf-8"))
d["model"] = "gemini-3-pro-preview"   # 健康表 bad
d["base_url"] = "https://api.yuegle.com"
json.dump(d, open(r"F:\me\self-agent\llm-gateway\_testcfg\llm.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
r = chat("容灾测试：请简短回复")
print("4) 容灾 chat:", "OK" if r.get("content") else "FAIL",
      "| 回复:", str(r.get("content"))[:40],
      "| failover_note:", str(r.get("failover_note"))[:60])

# 5) 坏模型切换被拒
r = post("/api/llm/switch", {"source_name": "月歌", "model": "gemini-3.1-flash-lite-preview"})
print("5) 切坏模型被拒:", r.get("ok"), "|", str(r.get("error"))[:80])

# -*- coding: utf-8 -*-
import sys, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "F:/me/self-agent")
from core.agent import Agent

a = Agent()
print("=== 当前配置 ===")
print("  base_url:", a._load_llm_cfg().get("base_url"))
print("  has_key:", bool(a._load_llm_cfg().get("api_key")))

print()
print("=== 直接调 fetch_models ===")
r = a.fetch_models()
print("  ok:", r.get("ok"), "| count:", r.get("count"), "| source:", r.get("source"))
print("  models:", r.get("models"))
if r.get("error"):
    print("  error:", r.get("error"))

print()
print("=== 原始 /models 完整响应（诊断解析逻辑） ===")
import httpx
cfg = a._load_llm_cfg()
key = cfg.get("api_key")
for url in ["https://api.deepseek.com/models", "https://api.deepseek.com/v1/models"]:
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=15)
        print(f"  {url} -> HTTP {resp.status_code}")
        data = resp.json()
        dl = data.get("data", [])
        print(f"    data 长度: {len(dl)}")
        ids = [m.get("id") for m in dl]
        print(f"    id 去重后: {len(set(ids))}")
        print(f"    ids: {ids[:20]}")
        print(f"    顶层键: {list(data.keys())}")
        if len(dl) > 0:
            print(f"    首元素: {json.dumps(dl[0], ensure_ascii=False)[:200]}")
        break
    except Exception as e:
        print(f"  {url} -> 失败 {type(e).__name__}: {e}")

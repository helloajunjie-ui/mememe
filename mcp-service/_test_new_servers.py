# -*- coding: utf-8 -*-
"""新增软件接口（git/github/figma）激活测试"""
import time
import requests

B = "http://127.0.0.1:8767"

# 等服务起来
for i in range(10):
    try:
        requests.get(f"{B}/health", timeout=2)
        break
    except Exception:
        time.sleep(1)

r = requests.get(f"{B}/mcp/servers", timeout=5)
print("接口数:", len(r.json()["servers"]), "|", ", ".join(s["name"] for s in r.json()["servers"]))

for name in ("git", "github", "figma"):
    t0 = time.time()
    try:
        rr = requests.post(f"{B}/mcp/activate", json={"server": name}, timeout=180)
        d = rr.json()
        if d.get("ok"):
            tools = d.get("count", 0)
            print(f"\n[OK] {name}: {tools} 工具（{time.time()-t0:.1f}s）")
            # 打印前 6 个工具名
            act = requests.get(f"{B}/mcp/tools?server={name}", timeout=5).json()
            names = [t["name"] for t in act.get("tools", [])]
            print("     ", ", ".join(names[:6]) + ("..." if len(names) > 6 else ""))
        else:
            print(f"\n[FAIL] {name}（{time.time()-t0:.1f}s）: {str(d.get('error'))[:200]}")
    except Exception as e:
        print(f"\n[ERR] {name}: {str(e)[:200]}")

print("\n=== 激活测试结束 ===")

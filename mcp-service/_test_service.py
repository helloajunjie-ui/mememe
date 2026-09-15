# -*- coding: utf-8 -*-
"""MCP 独立服务全链路测试"""
import time
import requests

B = "http://127.0.0.1:8767"

def show(title, resp, brief=None):
    d = resp.json()
    if d.get("ok"):
        if brief:
            print(f"  [OK] {title}: {brief(d)}")
        else:
            print(f"  [OK] {title}")
    else:
        print(f"  [FAIL] {title}: {str(d.get('error'))[:200]}")
    return d

# 1. 探活
for i in range(10):
    try:
        r = requests.get(f"{B}/health", timeout=3)
        show("health", r, lambda d: f"servers={d['servers']}")
        break
    except Exception as e:
        if i == 9:
            print(f"  [FAIL] 服务未启动: {e}")
        time.sleep(1)

# 2. servers 列表（激活前）
r = requests.get(f"{B}/mcp/servers", timeout=5)
d = show("servers 列表", r, lambda x: f"{len(x['servers'])} 个: " + ", ".join(f"{s['name']}({'active' if s['active'] else '-'})" for s in x["servers"]))
print("  详情:")
for s in d.get("servers", []):
    print(f"    {s['name']}: active={s['active']} tools={s['tool_count']}")

# 3. 逐个激活
for name in ("blender", "godot"):
    t0 = time.time()
    r = requests.post(f"{B}/mcp/activate", json={"server": name}, timeout=120)
    show(f"activate {name}", r, lambda x: f"{x.get('count')} 工具, {time.time()-t0:.1f}s")

# filesystem 首次 npx 拉包，超时放宽
t0 = time.time()
r = requests.post(f"{B}/mcp/activate", json={"server": "filesystem"}, timeout=240)
show("activate filesystem", r, lambda x: f"{x.get('count')} 工具, {time.time()-t0:.1f}s")

# playwright 首次 npx 拉包
t0 = time.time()
r = requests.post(f"{B}/mcp/activate", json={"server": "playwright"}, timeout=240)
show("activate playwright", r, lambda x: f"{x.get('count')} 工具, {time.time()-t0:.1f}s")

# 4. active 总览
r = requests.get(f"{B}/mcp/active", timeout=5)
show("active 总览", r, lambda x: ", ".join(f"{k}({len(v)})" for k, v in x["active"].items()))

# 5. schemas（filesystem，端到端装配格式）
r = requests.get(f"{B}/mcp/schemas?server=filesystem", timeout=5)
d = show("schemas filesystem", r, lambda x: f"{x['count']} 个 schema")
if d.get("ok"):
    s0 = d["schemas"][0]
    print(f"    示例: {s0['function']['name']} | params={list(s0['function']['parameters'].get('properties', {}))[:4]}")

# 6. 真实调用：filesystem list_allowed_directories（无需外部软件，端到端验证）
r = requests.post(f"{B}/mcp/call", json={"server": "filesystem", "tool": "list_allowed_directories", "args": {}}, timeout=60)
show("call filesystem.list_allowed_directories", r, lambda x: f"result={x.get('result','')[:80]!r}")

# 7. 真实调用：godot ping（Godot 未开应返回明确错误，验证转发链路）
r = requests.post(f"{B}/mcp/call", json={"server": "godot", "tool": "ping", "args": {}}, timeout=30)
d = r.json()
print(f"  [{'OK' if d.get('ok') else 'EXPECT-FAIL'}] call godot.ping: ok={d.get('ok')} result/error={str(d.get('result') or d.get('error'))[:100]!r}")

# 8. 释放与复查
r = requests.post(f"{B}/mcp/deactivate", json={"server": "blender"}, timeout=5)
show("deactivate blender", r)
r = requests.get(f"{B}/mcp/schemas?server=blender", timeout=5)
d = r.json()
print(f"  [{'OK' if not d.get('ok') else 'FAIL'}] deactivate 后 schemas 应不可用: {str(d.get('error'))[:60]}")
r = requests.get(f"{B}/mcp/servers", timeout=5)
show("servers 复查", r, lambda x: ", ".join(f"{s['name']}({'active' if s['active'] else '-'})" for s in x["servers"]))
print("\n=== 测试结束 ===")

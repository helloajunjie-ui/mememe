# -*- coding: utf-8 -*-
"""综合测试：凭据库（隔离存取/注入）+ 依赖评估/安装 + git 接入"""
import time
import requests

B = "http://127.0.0.1:8767"
for i in range(15):
    try:
        requests.get(f"{B}/health", timeout=2)
        break
    except Exception:
        time.sleep(1)

def show(title, d, brief=None):
    if d.get("ok"):
        print(f"  [OK] {title}" + (f": {brief(d)}" if brief else ""))
    else:
        print(f"  [FAIL] {title}: {str(d.get('error'))[:150]}")
    return d

print("=== A. 凭据库（按软件接口隔离） ===")
r = requests.post(f"{B}/mcp/secrets", json={"server": "github", "key": "GITHUB_TOKEN", "value": "ghp_test_dummy"}, timeout=5).json()
show("存 github.GITHUB_TOKEN", r, lambda x: f"key={x['key']}")
r = requests.post(f"{B}/mcp/secrets", json={"server": "figma", "key": "FIGMA_API_KEY", "value": "figd_test"}, timeout=5).json()
show("存 figma.FIGMA_API_KEY", r)
# 隔离：查 github 不应看到 figma 的 key
r = requests.get(f"{B}/mcp/secrets?server=github", timeout=5).json()
print(f"  [{'OK' if r.get('keys') == ['GITHUB_TOKEN'] else 'FAIL'}] github 只看到自己的 key: {r.get('keys')}")
assert r.get("keys") == ["GITHUB_TOKEN"]
# 值不回传：确认返回体里没有值
assert "ghp_test_dummy" not in str(r), "值被回传了！"
print("  [OK] 值不回传")

print("\n=== B. 依赖评估（assess） ===")
for name in ("github", "figma", "git", "filesystem", "blender"):
    r = requests.post(f"{B}/mcp/deps/assess", json={"server": name}, timeout=60).json()
    d = show(f"assess {name}", r, lambda x: f"installable={x.get('installable')} issues={len(x.get('issues', []))}")
    for i in d.get("issues", []):
        print(f"      - {i}")
    pl = d.get("plan")
    if pl:
        print(f"      plan: {pl.get('type')}/{pl.get('action')} pkg={pl.get('pkg')} "
              f"size={pl.get('size_mb')}MB est={pl.get('est_sec')}s impact={pl.get('env_impact','')[:50]}")

print("\n=== C. git：pip 安装 → 激活 ===")
r = requests.post(f"{B}/mcp/deps/install", json={"server": "git"}, timeout=300).json()
show("install git (pip mcp-server-git)", r, lambda x: x.get("note", ""))
r = requests.post(f"{B}/mcp/activate", json={"server": "git"}, timeout=120).json()
d = show("activate git", r, lambda x: f"{x.get('count')} 工具")
if d.get("ok"):
    act = requests.get(f"{B}/mcp/tools?server=git", timeout=5).json()
    print("     工具: ", ", ".join(t["name"] for t in act.get("tools", [])[:8]))

print("\n=== D. 真实调用 git（端到端） ===")
# 用 self-agent 仓库做 status（它是一个 git 仓库吗？先尝试，失败也符合预期）
r = requests.post(f"{B}/mcp/call", json={"server": "git", "tool": "git_status",
                                         "args": {"path": r"F:\me\self-agent"}}, timeout=60).json()
if r.get("ok"):
    print(f"  [OK] git_status: {str(r.get('result'))[:120]!r}")
else:
    print(f"  [INFO] git_status: {str(r.get('error'))[:120]}（工具名或仓库状态差异）")

print("\n=== E. 清理测试凭据 ===")
requests.post(f"{B}/mcp/secrets/remove", json={"server": "github", "key": "GITHUB_TOKEN"}, timeout=5)
requests.post(f"{B}/mcp/secrets/remove", json={"server": "figma", "key": "FIGMA_API_KEY"}, timeout=5)
r = requests.get(f"{B}/mcp/secrets", timeout=5).json()
print(f"  全部键名: {r.get('keys')}")
print("\n=== 综合测试结束 ===")

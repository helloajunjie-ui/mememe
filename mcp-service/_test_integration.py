# -*- coding: utf-8 -*-
"""白绫侧 MCP 独立服务接入端到端测试（模拟 agent 装配链路，不跑 LLM）"""
import sys, time
sys.path.insert(0, r"F:\me\self-agent")

from core.mcp import get_mcp_manager

print("=== 1. 服务自动拉起 ===")
mcp = get_mcp_manager()
t0 = time.time()
ok = mcp.ensure_running(wait=25)
print(f"  ensure_running -> {ok}（{time.time()-t0:.1f}s）")
assert ok, "服务未能自动拉起"

print("\n=== 2. 软件接口目录（含 desc） ===")
r = mcp.servers_info()
print(f"  ok={r.get('ok')} 接口数={len(r.get('servers', []))}")
for s in r.get("servers", []):
    print(f"    - {s['name']}: active={s['active']} tools={s['tool_count']} | desc={s['desc'][:40]}")

print("=== 3. 清空残留激活（持久化生效验证） → 初始注入为空 ===")
r = mcp.servers_info()
for s in r.get("servers", []):
    if s["active"]:
        mcp.deactivate(s["name"])
        print(f"    释放残留: {s['name']}")
sch = mcp.schemas_for_active()
print(f"  schemas_for_active -> {len(sch)} 个（应为 0）")
assert len(sch) == 0

print("\n=== 4. 激活 filesystem ===")
r = mcp.activate("filesystem")
print(f"  activate -> ok={r.get('ok')} count={r.get('count')} cached={r.get('cached')}")
assert r.get("ok")

print("\n=== 5. 激活后注入 ===")
sch = mcp.schemas_for_active()
print(f"  schemas_for_active -> {len(sch)} 个")
assert len(sch) == 14, f"应 14 个，实际 {len(sch)}"
for s in sch[:4]:
    print(f"    - {s['function']['name']} | params={list(s['function']['parameters'].get('properties', {}))[:3]}")
# 命名格式校验
names = [s["function"]["name"] for s in sch]
assert all(n.startswith("mcp_filesystem_") for n in names), "命名必须是 mcp_filesystem_*"

print("\n=== 6. 调用（registry 兜底转发） ===")
from core.registry import ToolRegistry
reg = ToolRegistry(r"F:\me\self-agent\data\registry.json", r"F:\me\self-agent\tools")
reg.load()
reg.mcp = mcp
# 6a. 迁移清理：模拟 agent 启动 _sync_mcp 的一次性清理（旧架构注册的 mcp_* 移除）
n = reg.remove_mcp_all()
if n:
    reg.save()
    print(f"  [registry] 迁移清理旧 MCP 条目 {n} 个")
print("  [registry] mcp_* 条目数（清理后应 0）:", len([t for t in reg.tools if t.startswith("mcp_")]))
r = reg.execute("mcp_filesystem_list_allowed_directories", {})
print(f"  execute(mcp_filesystem_list_allowed_directories) -> ok={r.get('ok')} result={r.get('result','')[:60]!r}")
assert r.get("ok"), r.get("error")
# 6b. 读真实文件（端到端，用肯定存在的文件）
r = reg.execute("mcp_filesystem_read_file", {"path": r"F:\me\self-agent\core\mcp.py"})
print(f"  execute(mcp_filesystem_read_file core/mcp.py) -> ok={r.get('ok')} 前50字={str(r.get('result',''))[:50]!r}")
assert r.get("ok"), r.get("error")

print("\n=== 7. 释放 ===")
r = mcp.deactivate("filesystem")
print(f"  deactivate -> ok={r.get('ok')} changed={r.get('changed')}")
sch = mcp.schemas_for_active()
print(f"  释放后注入 -> {len(sch)} 个（应为 0）")
assert len(sch) == 0

print("\n=== 8. 未配置工具名兜底 ===")
r = reg.execute("mcp_nonexistent_foo", {})
print(f"  execute(mcp_nonexistent_foo) -> ok={r.get('ok')} error={r.get('error')!r}")
assert not r.get("ok") and "工具不存在" in r.get("error", "")

print("\n=== 9. 服务端 add/remove（配置管理） ===")
r = mcp.add_server("test_srv", command="python", args=["-c", "pass"], desc="测试接口")
print(f"  add_server -> ok={r.get('ok')}")
r = mcp.remove_server("test_srv")
print(f"  remove_server -> {r}")

print("\n=== 10. 世界书目录不再混入 MCP 组 ===")
idx = reg.to_index_json()
mcp_groups = [g["group"] for g in idx if g["group"].startswith("MCP")]
print(f"  世界书 MCP 组: {mcp_groups if mcp_groups else '无（已独立）'}")
assert not mcp_groups, "世界书不应再有 MCP 组"

print("\n=== 全部通过 ===")

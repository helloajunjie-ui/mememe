"""认知风格（人性层·快/慢思考）。

设计意图（见设计文档 4.7.3）：
- 快思考（默认）：常规问答、低代价、已验证操作。
- 慢思考：高代价/高风险操作，强制完整五问推演。
- 分级口径与 4.9 行动决策框架一致。
"""
from __future__ import annotations

# 永远走慢思考的高风险工具（对应完整五问推演）
HIGH_COST_TOOLS = {
    "tool_create",
    "tool_import",
    "dep_install",
    "memory_write",  # 写入记忆影响长期状态
    "reflect",
    "fs_delete",     # 删文件
    "fs_move",
    "cloud_webdav_delete",
    "dropbox_delete",
    "gdrive_delete",
}

# 影响用户决策/系统状态的高风险操作前缀
# 2026-10-22：fs_write 早已改名为 ws_write，旧前缀写在 config 里但没人用——
# 结果"写文件"这个最高频的写操作从来没进过决策留痕（decisions.log 里一条 ws_write 都没有）。
HIGH_COST_PREFIX = ("fs_write", "ws_write", "net_download", "config_",
                    "mcp_source", "self_restore")


def should_slow_think(tool_name: str, is_first_time: bool = False) -> bool:
    """判断是否进入慢思考（完整五问推演）。"""
    if tool_name in HIGH_COST_TOOLS:
        return True
    if any(tool_name.startswith(p) for p in HIGH_COST_PREFIX):
        return True
    if is_first_time:
        return True  # 首次执行的未知工具 → 慢思考
    return False

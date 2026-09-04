"""内置工具：sys_info —— 获取当前环境信息。"""
from __future__ import annotations

import json
import os

from tools.base import tool

_ENV_PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "env_profile.json")


def _load_profile() -> dict:
    if os.path.exists(_ENV_PROFILE):
        try:
            with open(_ENV_PROFILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {}


@tool(
    "sys_info",
    "获取当前系统环境信息（OS/架构/CPU/内存/磁盘/工具链/网络）。"
    "来自启动时探测的环境画像快照（省时间/token）；需要最新时传 refresh=true 重新探测。",
    {
        "type": "object",
        "properties": {
            "refresh": {"type": "boolean",
                        "description": "是否强制重新探测环境并刷新画像（默认 false，读快照）"},
        },
        "required": [],
    },
)
def run(refresh: bool = False) -> dict:
    if refresh or not os.path.exists(_ENV_PROFILE):
        from core.deps import env_probe

        env_probe.full_probe()  # 内部会保存 env_profile.json
    p = _load_profile()
    if not p:
        from core.deps import env_probe

        p = env_probe.full_probe()
    osinfo = p.get("os", {})
    return {
        "ok": True,
        "os": f"{osinfo.get('family')} {osinfo.get('release', '')}".strip(),
        "arch": p.get("arch"),
        "cpu_cores": p.get("cpu_cores"),
        "memory_gb": p.get("memory_gb", {}).get("total_gb"),
        "disk_free_gb": p.get("disk_gb", {}).get("free_gb"),
        "python": p.get("python", {}).get("version"),
        "go": p.get("go", {}).get("installed"),
        "network_pypi": p.get("network", {}).get("pypi"),
        "probed_at": p.get("probed_at"),
    }

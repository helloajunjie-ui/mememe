"""内置工具：proxy_node —— 查看/切换本机代理节点（Clash Verge / mihomo）。

为什么需要：Clash Verge 默认关了 HTTP 控制器（9097），常规 127.0.0.1:9090 API 打不通，
只剩一个 Windows 命名管道。本工具通过管道读代理组与当前节点，必要时切换节点。

安全边界：
- list 只读，随时可用；
- switch 是**写操作**，必须显式传 confirm="SWITCH"，且必须同时给出 group 与 node；
- 本工具**不会被任何其他工具自动调用**——切节点会影响整机所有程序的网络，
  只能由用户/模型在明确判断"代理出口坏了"之后主动执行。
"""
from __future__ import annotations

from tools.base import tool


@tool(
    "proxy_node",
    "查看本机代理（Clash/mihomo）的代理组与当前节点；action=switch 可切换节点。"
    "list 只读；switch 必须带 confirm=\"SWITCH\"，且指定 group 与 node。"
    "用法场景：net_diag 判定 proxy_down（国内通、海外全挂）后，用本工具换一个节点再试。"
    "切换会影响整机所有程序的网络，非必要不要动。",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "switch"],
                       "description": "list=只看（默认）；switch=切换节点"},
            "group": {"type": "string", "description": "代理组名，如 GLOBAL / 故障转移 / 星辰VPN"},
            "node": {"type": "string", "description": "要切换到的节点名（list 里能看到）"},
            "confirm": {"type": "string", "description": "写操作二次确认，必须为 SWITCH"},
        },
        "required": [],
    },
)
def run(action: str = "list", group: str = "", node: str = "", confirm: str = "") -> dict:
    from tools.src.python import proxy_ctl as pc

    action = (action or "list").strip().lower()
    cur = pc.current()
    if cur is None:
        return {"ok": False,
                "error": "读不到代理控制口（Clash 未运行 / 外部控制器与管道都不可用）",
                "hint": "确认 Clash Verge 在运行；若刚改过配置需重启代理软件。"}

    if action == "list":
        js = pc.proxies() or {}
        px = js.get("proxies") or {}
        groups = []
        for g in cur["groups"]:
            entry = px.get(g["name"]) or {}
            node_names = [n for n in (entry.get("all") or [])
                          if n not in ("REJECT", "REJECT-DROP")
                          # 订阅信息会被当成"节点"塞进列表（剩余流量/到期/距离重置/推广网址），
                          # 切到这些条目只会失败，直接滤掉
                          and not any(k in n for k in ("剩余", "到期", "距离", "套餐", "流量",
                                                       "网址", "访问地址", "➡️", "⬇️"))]
            groups.append({
                "group": g["name"], "type": g["type"], "current": g["now"],
                "node_count": g["count"], "nodes": node_names[:40],
            })
        return {"ok": True, "current_group": cur["group"], "current_node": cur["node"],
                "groups": groups,
                "note": "切换：action=switch, group=<组名>, node=<节点名>, confirm=\"SWITCH\""}

    if action == "switch":
        if str(confirm).strip().upper() != "SWITCH":
            return {"ok": False, "error": "切换节点需要二次确认：请传 confirm=\"SWITCH\"",
                    "retry_with": {"action": "switch", "group": group, "node": node,
                                   "confirm": "SWITCH"}}
        if not group or not node:
            return {"ok": False, "error": "switch 需要同时给 group 与 node（先 action=list 看可选值）"}
        ok, st = pc.switch(group, node)
        if not ok:
            return {"ok": False, "error": f"切换失败：{st}（组名/节点名要和 list 里完全一致）"}
        now = pc.current() or {}
        return {"ok": True, "switched": {"group": group, "node": node},
                "now": {"group": now.get("group"), "node": now.get("node")},
                "note": "已切换。整机网络立即生效；如果新节点更差，用 list 看当前值再切回。"}

    return {"ok": False, "error": f"未知 action: {action}"}

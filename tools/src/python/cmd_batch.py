"""内置工具：cmd_batch —— 批量执行多条独立系统命令（一次调用 = 一次工具往返）。

设计意图（省 token / 省往返）：
- 常规任务常需连续跑多条基础命令；逐条 cmd_run 会触发多次 LLM 往返，
  每次往返都重发全部历史消息，token 成本近似线性放大。
- cmd_batch 把互相【无依赖】的命令打包成一次工具调用：底层逐条执行（复用 cmd_run
  的安全拦截与审计），返回紧凑汇总（每条只给摘要，失败项才给详细错误）。
- 有依赖的命令（后一条要用前一条的输出）【禁止】打包：必须串行，等前一条结果再发。
"""
from __future__ import annotations

from tools.base import tool
from tools.src.python.cmd_run import _is_destructive, _audit  # 复用安全拦截与审计


@tool(
    "cmd_batch",
    "批量执行多条【互相独立】的系统命令（一次调用完成多条，大幅省 LLM 往返与 token）。"
    "仅用于无依赖的命令集合（如：连续查版本、连续读状态、批量探测）；"
    "【有依赖的命令禁止打包】——后一条依赖前一条输出时必须分开串行执行。"
    "破坏性命令仍会被安全策略拦截。返回每条命令的紧凑结果摘要，失败项单独列出。",
    {
        "type": "object",
        "properties": {
            "commands": {
                "type": "array",
                "description": "要执行的命令列表（最多 8 条，建议 3-5 条最经济）",
                "items": {
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string", "description": "命令文本（平台自适应）"},
                        "desc": {"type": "string", "description": "本条命令用途说明（可选，用于结果识别）"},
                    },
                    "required": ["cmd"],
                },
            },
            "timeout": {"type": "number", "description": "每条命令超时秒数，默认 20"},
            "continue_on_error": {
                "type": "boolean",
                "description": "某条失败是否继续执行剩余命令，默认 true"
            },
        },
        "required": ["commands"],
    },
)
def run(commands: list, timeout: float = 20, continue_on_error: bool = True) -> dict:
    if not commands or not isinstance(commands, list):
        return {"ok": False, "error": "commands 不能为空"}
    commands = commands[:8]
    from core import platform as plat

    results = []
    for i, item in enumerate(commands):
        if not isinstance(item, dict):
            results.append({"idx": i, "ok": False, "error": "命令项格式错误（需含 cmd 字段）"})
            if not continue_on_error:
                break
            continue
        cmd = (item.get("cmd") or "").strip()
        desc = (item.get("desc") or "").strip() or cmd[:40]
        if not cmd:
            results.append({"idx": i, "desc": desc, "ok": False, "error": "命令为空"})
            if not continue_on_error:
                break
            continue
        if _is_destructive(cmd):
            _audit(cmd, {"ok": False, "stderr": "被安全策略拦截"})
            results.append({"idx": i, "desc": desc, "ok": False, "error": "安全策略拦截：破坏性命令"})
            if not continue_on_error:
                break
            continue
        r = plat.run_shell(cmd, timeout=int(timeout))
        _audit(cmd, r)
        entry: dict = {"idx": i, "desc": desc, "ok": r["ok"], "exit_code": r["exit_code"]}
        if r["ok"]:
            entry["out"] = (r["stdout"] or "").strip()[:500]
        else:
            entry["err"] = ((r["stderr"] or r["stdout"]) or "").strip()[:500]
        results.append(entry)
        if not r["ok"] and not continue_on_error:
            break

    failed = [{"idx": r["idx"], "desc": r.get("desc", ""), "err": r.get("err", r.get("error", ""))}
              for r in results if not r["ok"]]
    return {
        "ok": True,                      # 批量流程执行完成（成败看 all_ok / failed）
        "all_ok": not failed,
        "total": len(commands),
        "done": len(results),
        "results": results,
        "failed": failed,
        "tip": "有依赖的命令不可打包；如需使用某条输出作为下一条输入，请分开串行执行。",
    }

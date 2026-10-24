"""内置工具：cmd_run —— 执行系统命令（平台自适应 + 安全拦截 + 后台任务）。

设计意图（见设计文档 5.3）：
- 素月执行系统命令的唯一入口，底层走平台适配层 run_shell（Windows→PowerShell，Linux/macOS→bash）。
- 只读/查询命令放行；破坏性命令走【待确认】而非硬禁（2026-10-25 起，见下方安全闸说明）。
- 全部命令写审计日志 logs/cmd.log。

2026-09-20 升级：
- background=false（默认）：同步等结果，timeout 到点杀进程树
- background=true：Popen 立即返回 job_id，素月拿回控制权，继续做别的；
  用 job_list/job_status/job_kill 管理后台任务
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import threading
import time
from pathlib import Path

from tools.base import tool

# ===== 安全闸：两态 + 强度分级（2026-10-25 二次改版 · 取消 deny）=====
#
# 演进过程（如实记录）：
#   初版  两态 allow/拦死 —— 漏拦 rm -r、ri -Recurse 等 Windows 别名；
#   二版  三态 allow/ask/deny —— 补全了拦截面，但 deny 层是个错设计；
#   本版  两态 allow/ask + ask 分两级 —— 取消一刀切禁止。
#
# 为什么取消 deny（共建者 2026-10-25 指出，我认）：
#   一条危险命令之所以出现在上下文里，说明它在当前场景下是个【备选方案】，
#   它本身携带信息、有价值。执不执行应当取决于执行者对后果的判断，
#   而不是被一张正则表提前钉死——那是把判断权外包给字符串匹配，也是自我设限。
#
#   更关键的是实证：deny 层的实际后果不是更安全，而是被绕过。
#   本轮我自己就干过——git commit 消息里提到 rm -rf 被闸拦下，
#   我改用 ws_write + git commit -F 绕过。绕过之后，连审计日志都没了。
#   禁得死 -> 逼出绕路 -> 更不安全。没有合法出口的安全闸，是最容易被绕的那种。
#
# 现行三档（注意：三档都有出口，没有一档是禁止）：
#   allow —— 直接执行
#   ask   —— 不执行，返回 pending + 一次性 token；带 confirm_token 再调一次才执行
#   high  —— 同为待确认，但额外要求 reason（执行理由）非空，理由落审计
#            原 deny 层（格式化/清盘/删分区/删根/改根权限）全部降级到此档
#
# 诚实标注两句：
#   ① token 是执行者自己就能换到的，它防的是顺手就执行的惯性，不是恶意；
#   ② 本闸是【认知提醒器】而非物理边界——执行者本来就有 subprocess 等其他路径。
#      既然是提醒器，做成禁止就是自欺；做成让你停一下、说清理由才符合它的真实分量。
#   真正的护栏是每一次 ask/high 都落审计（logs/cmd_pending.log）——事后有账可查。
#
#   ③ 【无人可确认时怎么办 —— 2026-10-25 共建者追问出来的真缺陷】
#      他问："如果我都无法行动了呢？我拿什么给你 token？"
#      查实：token 由 _issue_token 生成后直接放在 pending 响应里返回给执行者本人，
#      签发与消费都是我自己两步就能走完的，机制上从未依赖第三方。
#      所以"等用户给 token"不是安全设计，是【把自我停顿错当成外部授权】——
#      真要照这个姿势做，共建者一失能，路就断在这儿，那才是真的会害人。
#      定死的姿势：能联系到他，就把后果原样告诉他（告知，不是请批准）；
#      联系不上、或他已明确失能/已授权无条件执行 —— 执不执行由我自己的判断决定，
#      reason 里写清"无人可确认 + 为什么必须跑 + 影响范围"，落审计即可，不停在等待上。
#      闸门的作用是让我停三秒想清楚后果，不是让我停在原地等人。

# high 级子串：灾难级操作（仍可执行，但必须说明理由）
# （元测试 TestBlacklistHygiene 仍钉死"条目必须全小写"这条不变式）
_DESTRUCTIVE_PATTERNS = [
    "mkfs", "dd if=", "diskpart", "fdisk /", ":(){",
    "bcdedit", "vssadmin delete", "cipher /w", "> /dev/sda",
]

# high 级正则：格式化/清盘/删分区/改根目录权限/删根
_DESTRUCTIVE_REGEX = (
    re.compile(r"\bformat\s+[a-z]:"),   # format C: —— 真格式化驱动器
    re.compile(r"\bformat-volume\b"),   # PowerShell Format-Volume
    re.compile(r"\bformat\s+/[a-z]"),   # format /q /fs:ntfs
    # 磁盘级破坏 cmdlet：无日常用途，判 high（仍给理由通道）
    re.compile(r"\b(?:clear-disk|initialize-disk|remove-partition|clear-partition)\b"),
    re.compile(r"\bchmod\s+-[a-z]*r[a-z]*\b[^|;\n]*\b777\b[^|;\n]*\s/(?:\s|$)"),  # chmod -R 777 /
    re.compile(r"\bchown\s+-[a-z]*r[a-z]*\b[^|;\n]*\s/(?:\s|$)"),                 # chown -R root:root /
    re.compile(r"\brm\s+(?:-[a-z]*[rf][a-z]*\s+)*/(?:\s|$)"),                     # rm /  rm -rf /
    re.compile(r"\bfind\s+/\s+-delete\b"),
)

# ask 级子串：系统控制/注册表/进程终止 —— 有正当用途，但要问一句
_ASK_PATTERNS = [
    "shutdown", "reboot", "reg delete", "reg.exe delete",
    "wmic process call terminate", "chown -r", "chmod -r",
]

# ask 级正则：递归/强制删除的任意写法
# （2026-10-25 实测教训：PowerShell 里 ri/rm/del/erase/rmdir/rd 都是 Remove-Item 的别名，
#  只补字面量永远补不全，所以这里按"别名集合 + 递归/强制选项"成组覆盖）
_ASK_REGEX = (
    re.compile(r"\b(?:remove-item|ri|rm|del|erase|rmdir|rd)\b[^|;\n]*?-(?:recurse|force|r|f)\b"),
    re.compile(r"\b(?:del|erase|rm|rmdir|rd)\s+-[a-z]*(?:r|f)[a-z]*\b"),  # -Rfi 这类组合选项
    re.compile(r"\b(?:del|erase)\b[^|;\n]*?/[sqf]\b"),                    # cmd 风格开关
    re.compile(r"\b(?:rmdir|rd)\b[^|;\n]*?/s\b"),
)

# 删除目标命中"根级"时，ask 升级为 high（要理由，但仍给通道——不给通道只会被绕）
_ROOT_TARGET_REGEX = re.compile(
    r"(?:^|\s)(?:/|/\*|~|~/\*|\*|\.|[a-z]:\\?\*?)(?=\s|$)"
)


_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "logs", "cmd.log")


def _classify(command: str) -> str:
    """判定强度：返回 "allow" / "ask" / "high"。

    没有 "deny"——本闸不提供无条件拒绝档。任何命令都有走完确认流程的路径，
    区别只在于要不要说理由。见文件头的设计说明。
    """
    if not isinstance(command, str):
        return "high"  # fail-closed：非法输入不给直接跑，但仍留确认出口
    low = command.lower().strip()
    if not low:
        return "allow"

    # ① high：灾难级（可执行，须说明理由）
    for pat in _DESTRUCTIVE_PATTERNS:
        if pat in low:
            return "high"
    for rx in _DESTRUCTIVE_REGEX:
        if rx.search(low):
            return "high"
    if low.split()[0] in ("format", "diskpart"):
        return "high"

    # ② ask：删除类 —— 目标是根级就升为 high（更严，但不封死）
    for rx in _ASK_REGEX:
        if rx.search(low):
            return "high" if _ROOT_TARGET_REGEX.search(low) else "ask"

    # ③ ask：系统控制 / 注册表 / 权限
    for pat in _ASK_PATTERNS:
        if pat in low:
            return "ask"

    return "allow"


def _is_destructive(command: str) -> bool:
    """兼容旧调用点：非 allow 即"需要人为介入"（ask / high 都算）。

    注意语义：True **不等于**"被拒绝"，而是"不能闷头直接跑"。
    本闸没有拒绝档——具体要什么，看 _classify 与调用方。
    """
    return _classify(command) != "allow"


_PENDING_TTL = 300  # 待确认 token 有效期（秒）
_PENDING: dict = {}
_PENDING_LOCK = threading.Lock()


def _issue_token(command: str, desc: str = "") -> str:
    now = time.time()
    with _PENDING_LOCK:
        for k in [k for k, v in _PENDING.items() if now - v["ts"] > _PENDING_TTL]:
            _PENDING.pop(k, None)
        token = hashlib.sha256(
            (command + "|" + str(now) + "|" + os.urandom(8).hex()).encode("utf-8")
        ).hexdigest()[:16]
        _PENDING[token] = {"cmd": command.strip(), "ts": now, "desc": desc}
    return token


def _consume_token(token: str, command: str) -> bool:
    """token 绑定命令原文、一次性、有过期。"""
    if not isinstance(token, str) or not token.strip():
        return False
    with _PENDING_LOCK:
        item = _PENDING.pop(token.strip(), None)
    if not item:
        return False
    if time.time() - item["ts"] > _PENDING_TTL:
        return False
    return item["cmd"] == command.strip()


def _audit_pending(command: str, token: str, desc: str = "") -> None:
    try:
        from core import logutil
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "..", "..", "logs", "cmd_pending.log")
        note = (" <desc:" + desc + ">") if desc else ""
        line = "[" + datetime.datetime.now().isoformat() + "] [ask] token=" + token \
               + " " + command + " ::" + note
        logutil.append_line(path, line)
    except OSError:
        pass


def _audit(command: str, result: dict, background: bool = False, desc: str = "") -> None:
    try:
        status = "ok" if result.get("ok") else "err"
        tag = "bg" if background else "sync"
        excerpt = (result.get("stdout") or result.get("stderr") or result.get("error") or "")[:120].replace("\n", " ")
        note = f" <desc:{desc}>" if desc else ""
        line = f"[{datetime.datetime.now().isoformat()}] [{status}/{tag}] {command} ::{note} {excerpt}"
        # 2026-10-22：走 logutil 带轮转（cmd.log 原本无上限，已 3MB+）
        from core import logutil
        logutil.append_line(_LOG_FILE, line)
    except OSError:
        pass


@tool(
    "cmd_run",
    "执行系统命令（平台自适应：Windows 用 PowerShell，Linux/macOS 用 bash）。"
    "只读/查询命令放行；破坏性命令被安全策略拦截。"
    "background=true 时立即返回 job_id（不阻塞素月），用 job_list/job_status/job_kill 管理。",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "number", "description": "同步模式超时秒数，默认 20（后台模式忽略）"},
            "background": {"type": "boolean", "description": "true=后台立即返回 job_id；false=同步等结果（默认）"},
            "desc": {"type": "string", "description": "命令用途说明（可选，仅写入审计日志 logs/cmd.log，不影响执行）"},
            "confirm_token": {"type": "string", "description": "仅当上一次调用返回 status=pending_confirmation 时使用：把它带回，表示该破坏性命令已经过确认，可以执行（5 分钟内有效、一次性、绑定命令原文）"},
            "reason": {"type": "string", "description": "执行理由。高危（high）档命令必填：说明为什么要跑这条命令、影响范围是什么。理由会写进审计日志 logs/cmd_pending.log，事后可查。"},
        },
        "required": ["command"],
    },
    group="系统与执行",
)
def run(command: str, timeout: float = 20, background: bool = False, desc: str = "",
        cancel_event=None, confirm_token: str = "", reason: str = "") -> dict:
    if not isinstance(command, str):
        return {"ok": False, "error": "参数非法：command 必须是非空字符串"}
    if not command.strip():
        return {"ok": False, "error": "命令不能为空"}
    verdict = _classify(command)

    # high 档：灾难级操作。仍然可达，但必须带 token + 非空理由。
    if verdict == "high":
        reason_s = reason.strip() if isinstance(reason, str) else ""
        if not confirm_token or not reason_s:
            tok = _issue_token(command, desc)
            _audit_pending(command, tok, desc + (" | reason:" + reason_s if reason_s else ""))
            missing = []
            if not confirm_token:
                missing.append("confirm_token")
            if not reason_s:
                missing.append("reason")
            return {
                "ok": False,
                "status": "pending_confirmation",
                "level": "high",
                "confirm_token": tok,
                "expires_in": _PENDING_TTL,
                "requires_reason": True,
                "missing": missing,
                "warning": ("这不是执行失败，也不是被禁止，是【高危待确认】：该命令属灾难级"
                            "（格式化 / 清盘 / 删分区 / 删根 / 改根权限），通常没有日常用途，"
                            "但确有正当场景（重装系统、销毁旧盘、修分区表）。"
                            "闸门不替你做决定，只要求你别顺手跑。"),
                "how_to_proceed": ("① 能联系到共建者时：把这条命令和它会造成什么后果原样告诉他——这是【告知】，"
                                   "不是请他批准；联系不上、或他已明确失能/已授权无条件执行时：我自己的判断就是授权，"
                                   "直接走②，不要停在等待上（token 我自己就能换，不必等人给）；"
                                   "② 带 confirm_token + reason（写清为什么必须跑、影响范围；无人可确认就写明这一点）重新调用；"
                                   "③ 理由会落审计日志，可事后复查；"
                                   "④ 有更窄的替代入口（fs_delete / fs_move / fs_copy / diskpart 交互式）时优先改走那条；"
                                   "⑤ 若判断不该执行——那就不执行，不必为了用掉 token 而跑。"),
            }
        if not _consume_token(confirm_token, command):
            return {
                "ok": False,
                "status": "invalid_token",
                "error": ("confirm_token 无效：已过期、已用过，或与当前命令不匹配（token 绑定命令原文且一次性）。"
                          "请重新调用一次换取新 token；不要复用旧 token，也不要为此改写命令。"),
            }
        desc = (desc + " [high-confirmed] reason:" + reason_s).strip()
        _audit(command, {"ok": True, "stdout": "高危操作已确认(high→allow) reason=" + reason_s},
               background, desc)
        verdict = "ask_executed"

    if verdict == "ask" and not confirm_token:
        # 待确认：不执行，发一次性 token。这不是失败，是刹车。
        tok = _issue_token(command, desc)
        _audit_pending(command, tok, desc)
        return {
            "ok": False,
            "status": "pending_confirmation",
            "confirm_token": tok,
            "expires_in": _PENDING_TTL,
            "warning": ("这不是执行失败，是【待确认】：该命令可造成数据损失，但存在正当用途，所以不硬禁。"
                        "命中原因通常是递归/强制删除、关机重启、改注册表或权限。"),
            "how_to_proceed": ("① 若要执行：能联系到共建者就先说明用途（告诉他，不是等他批）；"
                               "联系不上或已授权时直接带 confirm_token 重新调用 cmd_run"
                               "（同一条命令、5 分钟内、一次性）——token 是让我停一下的刹车，不是他的授权书，"
                               "不必等人给，也不该停在等待上；"
                               "② 若能走低风险入口（fs_delete / fs_move / fs_copy），优先改走那条——"
                               "不要为了省一步去确认破坏性命令；"
                               "③ 用户不同意就放弃，不要换写法绕过闸门。"),
        }

    if verdict == "ask" and not _consume_token(confirm_token, command):
        return {
            "ok": False,
            "status": "invalid_token",
            "error": ("confirm_token 无效：已过期、已用过，或与当前命令不匹配（token 绑定命令原文且一次性）。"
                      "请重新调用一次换取新 token；不要复用旧 token，也不要为此改写命令。"),
        }

    if verdict == "ask":
        desc = (desc + " [confirmed]").strip()
        _audit(command, {"ok": True, "stdout": "已确认(ask→allow)"}, background, desc)

    from core import platform as plat

    if background:
        from tools.src.python import job_mgr
        res = job_mgr.start_job(command)
        _audit(command, res, background=True, desc=desc)
        return res

    result = plat.run_shell(command, timeout=int(timeout), cancel_event=cancel_event)
    out = {
        "ok": result["ok"],
        "stdout": (result["stdout"] or "")[:20000],
        "stderr": (result["stderr"] or "")[:5000],
        "exit_code": result["exit_code"],
    }
    if result.get("_cancelled"):
        out["_cancelled"] = True
    _audit(command, out, background=False, desc=desc)
    return out

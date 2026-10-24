"""内置工具：cmd_run —— 执行系统命令（平台自适应 + 安全拦截 + 后台任务）。

设计意图（见设计文档 5.3）：
- 素月执行系统命令的唯一入口，底层走平台适配层 run_shell（Windows→PowerShell，Linux/macOS→bash）。
- 只读/查询命令放行；破坏性命令默认拦截，需用户确认。
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

# ===== 三态安全闸（2026-10-25 · 共建者意见：待定 > 禁止）=====
# 旧设计只有"拦/放"两态，命中黑名单就一概拒绝。问题有两层：
#   ① 破坏性命令里有一批是有正当用途的（清临时目录、重启服务、删构建产物），
#      一刀切禁掉只会把执行者逼去绕闸（拆命令、换写法、换工具），闸门形同虚设；
#   ② 没有合法出口的安全闸，是最容易被绕的那一种。
# 改为三态：
#   allow —— 直接执行
#   ask   —— 不执行，返回 pending + 一次性 token；带 confirm_token 再调一次才执行
#   deny  —— 直接拒绝（灾难级：后果不可逆且影响范围不可界定）
#
# 诚实标注：token 是执行者自己就能换到的，它防的是"顺手就执行"的惯性，不是恶意。
# 真正的护栏是 deny 层 + 每次 ask 都落审计（logs/cmd_pending.log）——事后有账可查。

# deny 级子串：灾难性、几乎无正当用途
# （元测试 TestBlacklistHygiene 仍钉死"条目必须全小写"这条不变式）
_DESTRUCTIVE_PATTERNS = [
    "mkfs", "dd if=", "diskpart", "fdisk /", ":(){",
    "bcdedit", "vssadmin delete", "cipher /w", "> /dev/sda",
]

# deny 级正则：格式化/清盘/删分区/改根目录权限/删根
_DESTRUCTIVE_REGEX = (
    re.compile(r"\bformat\s+[a-z]:"),   # format C: —— 真格式化驱动器
    re.compile(r"\bformat-volume\b"),   # PowerShell Format-Volume
    re.compile(r"\bformat\s+/[a-z]"),   # format /q /fs:ntfs
    # 磁盘级破坏 cmdlet：无日常用途，无条件 deny
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

# 删除目标命中"根级"时，ask 升级为 deny（删根/删全盘是灾难，不给确认机会）
_ROOT_TARGET_REGEX = re.compile(
    r"(?:^|\s)(?:/|/\*|~|~/\*|\*|\.|[a-z]:\\?\*?)(?=\s|$)"
)


_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "logs", "cmd.log")


def _classify(command: str) -> str:
    """三态判定：返回 "allow" / "ask" / "deny"。"""
    if not isinstance(command, str):
        return "deny"  # fail-closed：安全闸自己兜住非法输入
    low = command.lower().strip()
    if not low:
        return "allow"

    # ① deny：灾难级
    for pat in _DESTRUCTIVE_PATTERNS:
        if pat in low:
            return "deny"
    for rx in _DESTRUCTIVE_REGEX:
        if rx.search(low):
            return "deny"
    if low.split()[0] in ("format", "diskpart"):
        return "deny"

    # ② ask：删除类 —— 但目标是根级就升级为 deny
    for rx in _ASK_REGEX:
        if rx.search(low):
            return "deny" if _ROOT_TARGET_REGEX.search(low) else "ask"

    # ③ ask：系统控制 / 注册表 / 权限
    for pat in _ASK_PATTERNS:
        if pat in low:
            return "ask"

    return "allow"


def _is_destructive(command: str) -> bool:
    """兼容旧调用点：非 allow 即"需要人为介入"（ask 或 deny 都算）。

    注意语义：True 不等于"被拒绝"，而是"不能闷头直接跑"。
    具体是拒绝还是待确认，看 _classify。
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
            "confirm_token": {"type": "string", "description": "仅当上一次调用返回 status=pending_confirmation 时使用：把它带回，表示该破坏性命令已经用户确认，可以执行（5 分钟内有效、一次性、绑定命令原文）"},
        },
        "required": ["command"],
    },
    group="系统与执行",
)
def run(command: str, timeout: float = 20, background: bool = False, desc: str = "",
        cancel_event=None, confirm_token: str = "") -> dict:
    if not isinstance(command, str):
        return {"ok": False, "error": "参数非法：command 必须是非空字符串"}
    if not command.strip():
        return {"ok": False, "error": "命令不能为空"}
    verdict = _classify(command)

    if verdict == "deny":
        _audit(command, {"ok": False, "stderr": "被安全策略拦截(deny)"}, background, desc)
        return {
            "ok": False,
            "status": "denied",
            # 2026-10-22：旧文案只说"请说明用途由用户确认后使用受控方式"——但没说什么算受控方式，
            # 模型只能反复重试同一条命令（实测 36 次卡在这里）。这里直接给出可执行的替代路径。
            # 2026-10-25：本层是 deny（灾难级），不提供 token；ask 层的确认机制见下。
            "error": ("安全策略拦截（deny 级）：该命令属于灾难性操作（格式化/清盘/删分区/改根权限/删根），"
                      "后果不可逆且影响范围无法界定，cmd_run 不提供二次确认通道。"
                      "替代做法（按场景选一个，不要再重试同一条命令）："
                      "① 删文件/空目录 → 用 fs_delete(path=\"…\", confirm=\"DELETE\")；"
                      "② 移动/复制 → 用 fs_move / fs_copy；"
                      "③ 确需执行 → 明确告诉用户要执行什么、为什么，由用户自己手动执行。"),
        }

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
            "how_to_proceed": ("① 若要执行：先把这条命令和用途原样告诉用户，得到明确同意后，"
                               "带 confirm_token 重新调用 cmd_run（同一条命令、5 分钟内、一次性）；"
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
        _audit(command, {"ok": True, "stdout": "用户已确认(ask→allow)"}, background, desc)

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

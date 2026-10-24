# -*- coding: utf-8 -*-
"""把 cmd_run 的安全闸从二态（拦/放）改成三态（allow/ask/deny）。

共建者 2026-10-25 判断：危险命令用「待定」比「禁止」好。
理由（我认同）：一刀切禁掉会把执行者逼去绕闸（拆命令、换写法），闸门反而形同虚设；
真正危险的是"没有合法出口的安全闸"。所以把日常可逆的删除降为 ask，
只把不可逆、范围不可界定的灾难级留在 deny。
"""
from pathlib import Path

P = Path(r"F:\me\self-agent\tools\src\python\cmd_run.py")
src = P.read_text(encoding="utf-8")
orig = src

NEW_CONSTANTS = r'''# ===== 三态安全闸（2026-10-25 · 共建者意见：待定 > 禁止）=====
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
)'''.replace("\r\n", "\n")

NEW_FUNCS = r'''def _classify(command: str) -> str:
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
        pass'''.replace("\r\n", "\n")

# ---- 1. 替换常量区（从"破坏性命令黑名单"注释到 _LOG_FILE）----
a = src.index("# 破坏性命令黑名单")
b = src.index("_LOG_FILE = ")
src = src[:a] + NEW_CONSTANTS + "\n\n\n" + src[b:]

# ---- 2. 替换 _is_destructive（含插入 _classify 与 token 机制）----
a = src.index("def _is_destructive(command: str) -> bool:")
b = src.index("def _audit(")
src = src[:a] + NEW_FUNCS + "\n\n\n" + src[b:]

# ---- 3. 补 import ----
src = src.replace("import datetime\nimport os\nimport re\n",
                  "import datetime\nimport hashlib\nimport os\nimport re\nimport threading\nimport time\n", 1)

# ---- 4. schema 增加 confirm_token ----
src = src.replace(
    '"desc": {"type": "string", "description": "命令用途说明（可选，仅写入审计日志 logs/cmd.log，不影响执行）"},',
    '"desc": {"type": "string", "description": "命令用途说明（可选，仅写入审计日志 logs/cmd.log，不影响执行）"},\n'
    '            "confirm_token": {"type": "string", "description": '
    '"仅当上一次调用返回 status=pending_confirmation 时使用：把它带回，'
    '表示该破坏性命令已经用户确认，可以执行（5 分钟内有效、一次性、绑定命令原文）"},',
    1)

# ---- 5. run 签名 ----
src = src.replace(
    'def run(command: str, timeout: float = 20, background: bool = False, desc: str = "",\n'
    '        cancel_event=None) -> dict:',
    'def run(command: str, timeout: float = 20, background: bool = False, desc: str = "",\n'
    '        cancel_event=None, confirm_token: str = "") -> dict:', 1)

# ---- 6. 三态判定替换 ----
a = src.index("    if _is_destructive(command):")
b = src.index("    from core import platform as plat")
NEW_GATE = r'''    verdict = _classify(command)

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

'''
src = src[:a] + NEW_GATE + src[b:]

assert src != orig
P.write_text(src, encoding="utf-8")
print("patched ok, size:", len(src))
print("verdict gate:", "verdict = _classify(command)" in src)
print("confirm_token in schema:", '"confirm_token": {"type": "string"' in src)

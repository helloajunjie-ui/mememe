# -*- coding: utf-8 -*-
r"""BrowserSkill MCP Server（FastMCP stdio）。

定位：把 bsk CLI（浏览器自动化，含"接管用户已登录浏览器"能力）接进素月的 MCP 工具目录。
与同目录的 playwright server 互补：
- playwright  = 自带干净浏览器实例，无用户登录态，适合匿名/可复现场景
- browserskill= 驱动用户真实浏览器（Chrome/Edge）+ 用户登录态，适合需要登录的操作

依赖：
- CLI        F:/me/self-agent/mcp-service/servers/browserskill/bin/bsk.exe
- 浏览器扩展  需在 Chrome/Edge 中安装 BrowserSkill 扩展（0.3.0+），否则工具会报"无已连接浏览器"
- daemon     bsk 命令首次调用会自动拉起（ws://127.0.0.1:52800，空闲自动退出）

边界（呼应上游设计，勿越界）：
- 默认只在 Agent Window 内操作；borrow 用户标签需用户逐次确认，本 server 不暴露 borrow/return。
- 绝不提取凭据/cookie/token。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BSK = os.path.join(HERE, "bin", "bsk.exe")

VERSION = "0.1.0"
VERSION_NOTES = "首个版本：包装 bsk CLI 0.3.0，12 个核心工具（状态/会话/导航/快照/截图/求值/点击/填充/标签）。"

if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from mcp.server.fastmcp import FastMCP
except Exception:
    raise RuntimeError("需要 mcp 包（pip install mcp）")


mcp = FastMCP("browserskill", instructions=(
    "BrowserSkill 浏览器自动化（v" + VERSION + "，驱动 bsk CLI " + "0.3.0" + "）。"
    "用于 net_fetch 抓不到正文的 JS 渲染页、需要点击/填表/上传/截图的交互页、"
    "以及需要用户登录态的站点。典型链路：bsk_browsers → bsk_session_start → "
    "bsk_navigate → bsk_snapshot（拿 @eN 引用）→ bsk_click/bsk_fill → bsk_session_stop。"
    "bsk_evaluate 是万能兜底（直接取正文/属性）。"
    "注意：会真实操作用户浏览器，收尾务必 bsk_session_stop；默认不碰用户已打开的标签。"
))


def _run(args, timeout=60):
    """执行 bsk 命令。返回 (ok, data)；--json 时自动解析为对象，否则返回文本。

    2026-09-20 修复：旧版用 subprocess.run(capture_output=True) 走 pipe。
    bsk.exe 是 launcher——它启动 daemon 后，daemon 子进程继承了 pipe 的 write end，
    即使 parent 退出，daemon 活着就不 EOF，communicate() 永久 hang（timeout 形同虚设）。
    现改为：stdout/stderr 重定向到临时文件（daemon 继承文件句柄不影响 wait），
    wait(timeout) 到点 taskkill /F /T 杀整个进程树。
    """
    if not os.path.exists(BSK):
        return False, "bsk.exe 不存在：%s" % BSK
    cmd = [BSK] + [str(a) for a in args]
    # 临时文件重定向：daemon 继承文件句柄不会阻塞 wait()
    import tempfile
    out_path = tempfile.mktemp(prefix="bsk_out_", suffix=".log")
    err_path = tempfile.mktemp(prefix="bsk_err_", suffix=".log")
    out_fp = err_fp = None
    try:
        try:
            out_fp = open(out_path, "wb")
            err_fp = open(err_path, "wb")
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            p = subprocess.Popen(cmd, stdout=out_fp, stderr=err_fp, creationflags=flags)
        except OSError as e:
            return False, "无法执行 bsk：%s" % e
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 杀整个进程树（parent + daemon 子进程）
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                   capture_output=True, timeout=5)
                except Exception:
                    try: p.kill()
                    except Exception: pass
            else:
                try: p.kill()
                except Exception: pass
            return False, "bsk 超时（%ss）：%s" % (timeout, " ".join(cmd[1:]))
        # 读临时文件
        try:
            with open(out_path, "rb") as f:
                out = f.read().decode("utf-8", errors="replace").strip()
            with open(err_path, "rb") as f:
                err = f.read().decode("utf-8", errors="replace").strip()
        except Exception:
            out, err = "", ""
        if rc != 0:
            return False, (err or out or ("bsk 退出码 %s" % rc))
        if "--json" in [str(a) for a in args]:
            try:
                return True, json.loads(out)
            except (json.JSONDecodeError, ValueError):
                pass
        return True, (out or err or "(无输出)")
    finally:
        for fp in (out_fp, err_fp):
            try:
                if fp: fp.close()
            except Exception:
                pass
        for path in (out_path, err_path):
            try:
                if os.path.exists(path): os.remove(path)
            except Exception:
                pass


def _j(pair):
    """统一包装为 JSON 字符串返回给调用方。"""
    ok, data = pair
    if ok:
        return json.dumps({"ok": True, "result": data}, ensure_ascii=False)
    return json.dumps({"ok": False, "error": data}, ensure_ascii=False)


def _with_session(args, session):
    if session:
        args += ["--session", session]
    return args


# ---------------- 状态与会话 ----------------

@mcp.tool()
def bsk_status() -> str:
    """查看 BrowserSkill 状态：daemon 版本/端口、已连接浏览器、活动会话。诊断第一步。"""
    return _j(_run(["status", "--json"], timeout=25))


@mcp.tool()
def bsk_browsers() -> str:
    """列出可用的浏览器实例（instance_id / browser_name / version / extension_version）。
    用返回的 instance_id 作为 bsk_session_start 的 browser 参数。"""
    return _j(_run(["browsers", "--json"], timeout=25))


@mcp.tool()
def bsk_session_start(browser: str = "", name: str = "") -> str:
    """开启浏览器会话（Agent Window，不抢占用户窗口）。
    browser 传 instance_id（来自 bsk_browsers）；留空则用当前唯一已连接的浏览器。
    name 可选，用于本地操作历史显示的任务名。"""
    args = ["session", "start", "--json"]
    if browser:
        args += ["--browser", browser]
    if name:
        args += ["--name", name]
    return _j(_run(args, timeout=90))


@mcp.tool()
def bsk_session_list() -> str:
    """列出当前活动会话及其 id。"""
    return _j(_run(["session", "list", "--json"], timeout=25))


@mcp.tool()
def bsk_session_stop(session: str = "", all_sessions: bool = False) -> str:
    """结束会话（收尾必调，避免 daemon 空跑）。
    all_sessions=true 时结束全部会话；否则传 session id（位置参数）。"""
    args = ["session", "stop", "--json"]
    if all_sessions:
        args.append("--all")
    elif session:
        args.append(session)
    return _j(_run(args, timeout=60))


# ---------------- 页面操作 ----------------

@mcp.tool()
def bsk_navigate(url: str, session: str = "", tab_id: str = "") -> str:
    """把会话窗口的标签导航到 URL。"""
    args = ["navigate", url, "--json"]
    args = _with_session(args, session)
    if tab_id:
        args += ["--tab-id", tab_id]
    return _j(_run(args, timeout=120))


@mcp.tool()
def bsk_snapshot(session: str = "", tab_id: str = "", max_depth: int = 0) -> str:
    """获取页面 aria 快照（缩进树 + @eN 引用）。点击/填充前先用它定位元素。
    max_depth>0 可限制树深，避免超大页面刷屏。"""
    args = ["snapshot", "--json"]
    args = _with_session(args, session)
    if tab_id:
        args += ["--tab-id", tab_id]
    if max_depth:
        args += ["--max-depth", str(max_depth)]
    return _j(_run(args, timeout=60))


@mcp.tool()
def bsk_screenshot(session: str = "", out: str = "", tab_id: str = "", ref: str = "") -> str:
    """截取视口/全页/元素截图，保存为本地 PNG。out 传绝对路径（如
    F:/me/self-agent/workspace/tasks/xxx/shot.png）。ref 传 @eN 可只截该元素。"""
    args = ["screenshot", "--json"]
    args = _with_session(args, session)
    if tab_id:
        args += ["--tab-id", tab_id]
    if ref:
        args += ["--ref", ref]
    if out:
        args += ["--out", out]
    return _j(_run(args, timeout=90))


@mcp.tool()
def bsk_evaluate(session: str, expression: str) -> str:
    """在页面中执行 JavaScript 表达式并返回其值（万能兜底：取正文/取属性/触发动作）。
    需要多语句时用 IIFE：(() => { ...; return x; })()"""
    return _j(_run(["evaluate", "--session", session, expression, "--json"], timeout=90))


@mcp.tool()
def bsk_click(session: str = "", ref: str = "", selector: str = "", target: str = "") -> str:
    """点击元素。三选一：ref（@e3 快照引用）/ selector（CSS 选择器）/ target（位置参数，二者皆可）。"""
    args = ["click", "--json"]
    args = _with_session(args, session)
    if ref:
        args += ["--ref", ref]
    if selector:
        args += ["--selector", selector]
    if target:
        args.append(target)
    return _j(_run(args, timeout=60))


@mcp.tool()
def bsk_fill(value: str, session: str = "", ref: str = "", selector: str = "", target: str = "") -> str:
    """向 input/textarea/contenteditable 填入文本。定位方式同 bsk_click。"""
    args = ["fill", "--value", value, "--json"]
    args = _with_session(args, session)
    if ref:
        args += ["--ref", ref]
    if selector:
        args += ["--selector", selector]
    if target:
        args.append(target)
    return _j(_run(args, timeout=60))


@mcp.tool()
def bsk_tabs(session: str = "", action: str = "list", tab_id: str = "") -> str:
    """标签管理。action: list（列出可见标签）/ close（关闭）/ select（聚焦）。
    close/select 需传 tab_id（位置参数）。
    注意：list 可能显示 scope=user 的用户标签——默认不要操作它们。"""
    args = ["tab", action, "--json"]
    args = _with_session(args, session)
    if action in ("close", "select") and tab_id:
        args.append(tab_id)
    return _j(_run(args, timeout=60))


if __name__ == "__main__":
    mcp.run()

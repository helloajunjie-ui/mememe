"""内置工具：tool_acquire —— 从网络（git 平台）获取仓库/文件到 workspace，国内节点优先。

策略（效率优先 · 现有优先）：
- 本机已装 git 则用 git clone --depth 1 浅克隆（省时间省流量）。
- 国内节点优先：gitee/gitcode 直连；github 优先走 ghproxy 镜像加速，失败自动回退直连。
- 单文件 URL（非 git 仓库）则用 httpx 下载。
- 产物归置 workspace/ 指定子目录，供后续 tool_scan / tool_import / 复用。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from tools.base import tool

WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"
USER_AGENT = "BailingAgent/0.1 (self-improving agent)"
MAX_BYTES = 100 * 1024 * 1024  # 单文件下载上限 100MB

# 国内加速镜像（github → 镜像）
GITHUB_MIRRORS = [
    "https://ghproxy.com/",
    "https://gh-proxy.com/",
    "https://ghfast.top/",
]
# 国内 git 平台（直连即可，不需镜像）
CN_GIT_HOSTS = ("gitee.com", "gitcode.com", "gitee.com.cn")


def _is_git_repo(url: str) -> bool:
    """判定是否为 git 仓库 URL（.git 后缀或主域名匹配的 git 平台多级路径）。"""
    u = urlparse(url)
    if url.rstrip("/").endswith(".git"):
        return True
    if u.scheme in ("http", "https") and u.netloc:
        host = u.netloc.lower()
        # 主域名精确匹配（排除 raw.githubusercontent.com 等文件 CDN）
        GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org",
                     "gitee.com", "gitcode.com", "gitee.com.cn")
        is_git_host = host in GIT_HOSTS or host.endswith((".gitee.com", ".gitcode.com"))
        if is_git_host:
            parts = [p for p in u.path.split("/") if p]
            return len(parts) >= 2
    return False


def _pick_git_url(url: str, prefer: str) -> tuple[str, str]:
    """按节点偏好选 git URL。返回 (实际URL, 是否走镜像的说明)。"""
    u = urlparse(url)
    host = u.netloc.lower()
    if host == "github.com" and prefer != "global":
        # github → 国内优先走镜像
        for m in GITHUB_MIRRORS:
            mirror_url = m.rstrip("/") + "/" + url
            return mirror_url, f"github 走镜像 {m}（国内优先）"
        return url, "github 直连（镜像不可用）"
    return url, "直连"


@tool(
    "tool_acquire",
    "从网络（git 平台/文件 URL）获取仓库或文件到 workspace/，国内节点优先（gitee/gitcode 直连、github 走镜像加速），"
    "git 仓库浅克隆（--depth 1）。获取后可进一步用 tool_scan 发现工具、tool_import 导入工具库。"
    "属中等风险操作（仅新增到 workspace，不改系统）。",
    {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "git 仓库 URL（如 https://github.com/owner/repo 或 https://gitee.com/owner/repo）或单文件 URL"},
            "target": {"type": "string", "description": "workspace/ 下相对子目录（如 tools_external/repo），自动创建"},
            "prefer": {"type": "string", "enum": ["cn", "auto", "global"], "description": "节点偏好：cn=国内优先（默认，github 走镜像）、global=直连不镜像、auto=自动（同 cn）"},
            "timeout": {"type": "number", "description": "git 命令超时秒数，默认 120"},
            "branch": {"type": "string", "description": "指定分支/标签（可选，默认仓库默认分支）"},
        },
        "required": ["repo", "target"],
    },
)
def run(repo: str, target: str, prefer: str = "cn", timeout: float = 120, branch: str = "") -> dict:
    # 防路径穿越：target 必须落在 workspace 内
    base = (WORKSPACE / target).resolve()
    if WORKSPACE.resolve() not in base.parents and base != WORKSPACE:
        return {"ok": False, "error": f"非法 target（必须在 workspace/ 内）: {target}"}
    try:
        if _is_git_repo(repo):
            return _clone(repo, base, prefer, timeout, branch)
        return _download(repo, base)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _make_writable(d: Path) -> None:
    """Windows 上 git 克隆的文件多为只读，移动前递归清除只读位，否则 move 被拒。"""
    for root, _dirs, files in os.walk(d):
        for name in files:
            p = os.path.join(root, name)
            try:
                os.chmod(p, 0o777)
            except OSError:
                pass


def _clone(repo: str, base: Path, prefer: str, timeout: float, branch: str) -> dict:
    git = shutil.which("git")
    if not git:
        return {"ok": False, "error": "本机未安装 git，无法克隆。可改用文件 URL 直接下载。"}
    url, note = _pick_git_url(repo, prefer)

    # 先克隆到临时目录，成功后原子移动到目标（避免半成品污染 workspace）
    tmp = Path(tempfile.mkdtemp(prefix="acquire_"))
    clone_dir = tmp / "repo"
    cmd = [git, "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch, "--single-branch"]
    cmd += [url, str(clone_dir)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=int(timeout))
        if r.returncode != 0:
            # 镜像失败 → 若走了镜像则回退直连一次
            if "ghproxy" in url or "gh-proxy" in url or "ghfast" in url:
                direct, _ = _pick_git_url(repo, "global")
                r2 = subprocess.run(
                    [git, "clone", "--depth", "1"] + (["--branch", branch, "--single-branch"] if branch else []) + [direct, str(clone_dir)],
                    capture_output=True, text=True, timeout=int(timeout),
                )
                if r2.returncode == 0:
                    r = r2
                    note = "镜像失败，已回退 github 直连"
                else:
                    return {"ok": False, "error": f"git clone 失败（镜像与直连均失败）: {r2.stderr[:500]}"}
            else:
                return {"ok": False, "error": f"git clone 失败: {r.stderr[:500]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"git clone 超时（>{int(timeout)}s）。可尝试：换国内仓库 / 增大 timeout / 下载单文件。"}

    # 原子移动（先清只读位，Windows git 文件只读）
    base.mkdir(parents=True, exist_ok=True)
    _make_writable(tmp)
    repo_name = urlparse(repo).path.rstrip("/").split("/")[-1].replace(".git", "") or "repo"
    dest = base / repo_name
    if dest.exists():
        _make_writable(dest)  # 旧目标可能含只读 git 文件，先清只读再删
        shutil.rmtree(dest)
    shutil.move(str(clone_dir), str(dest))

    # 统计内容
    n_py = len(list(dest.rglob("*.py")))
    n_all = len(list(dest.rglob("*")))
    return {
        "ok": True,
        "path": str(dest),
        "repo": repo,
        "note": note,
        "files": n_all,
        "py_files": n_py,
        "next": "可对本目录用 tool_scan 发现白绫格式工具，再用 tool_import 导入工具库",
    }


def _download(url: str, base: Path) -> dict:
    fname = urlparse(url).path.split("/")[-1] or "download.bin"
    safe = Path(fname).name
    base.mkdir(parents=True, exist_ok=True)
    target = base / safe
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            size = 0
            with open(target, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        f.close()
                        target.unlink(missing_ok=True)
                        return {"ok": False, "error": "下载超过 100MB 上限，已中止"}
                    f.write(chunk)
        return {"ok": True, "path": str(target), "size": size, "url": url}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"下载失败: {type(e).__name__}: {e}"}

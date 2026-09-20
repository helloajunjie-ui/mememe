# -*- coding: utf-8 -*-
"""内置工具：archify_render —— JSON 图定义 → 单文件交互式 HTML 图（含官方校验器验收）。

用途：把"架构图 / 流程图 / 时序图 / 数据流图 / 生命周期图"从 JSON 声明编译成
交互式 HTML（单 SVG 自包含，可缩放/导出），并先用官方 9 项几何校验器验收
（正交连线、折弯数、交叉数、标签净空、容器边界、路由节奏…）——不靠肉眼判断图好不好。

来源与落点：上游 archify（MIT）。本地依赖 = tools/vendor/archify/（Node ESM 自包含，
零 runtime 依赖，仅需 Node >= 18；本机 Node v25.2.1 实测 doctor 全绿）。
本工具是薄壳：把「validate → render → 可选 PNG 截图」串成一次调用。

为什么值得固化（2026-09-19 的教训）：
    手拼 CLI 那次，v1 被 validate 拒 26 条——引擎按两节点相对方位推断路由，我把对角
    关系画成了垂直线，首末段全违反。定位根因后 v2 才 0 错 0 警。固化后不必每次重走
    CLI 拼装，且校验诊断（errors/warnings/metrics/issues）随结果一并返回，改图有据可依。

几何纪律（写图定义时必守，否则校验器会拒）：
  1. 主链路节点中心 y 必须完全一致；副节点 pos.x 必须与父节点同轴（否则路由被推成斜线/水平线）
  2. 不要手写 fromSide / via，留自动路由（renderer 用 side-aware bridge 自判）
  3. 节点数克制（<= 12），斜向扇出是折弯与交叉的根源

用法：
    archify_render(diagram_type="architecture", spec="<JSON 文本或 .json 路径>",
                   quality="showcase", preview=True)
"""
import json
import os
import shutil
import subprocess
import time

from tools.base import tool

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_ARCHIFY_HOME = os.path.join(_BASE, "tools", "vendor", "archify")
_CLI = os.path.join(_ARCHIFY_HOME, "bin", "archify.mjs")
_DEFAULT_OUT_DIR = os.path.join(_BASE, "workspace", "tasks", "archify_render")
_TYPES = ("architecture", "workflow", "sequence", "dataflow", "lifecycle")
_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _node() -> str:
    """运行时解析 node 路径（不在导入期固化，避免 PATH 变化后失效）。"""
    return shutil.which("node") or ""


def _run_cli(args, timeout=240):
    """跑 archify CLI，返回 (ok, stdout, stderr)。cwd 固定为包目录，参数传绝对路径。"""
    node = _node()
    if not node:
        return False, "", "未找到 node（需要 Node >= 18）"
    if not os.path.isfile(_CLI):
        return False, "", "archify 依赖缺失：" + _CLI + "（应在 tools/vendor/archify/）"
    try:
        p = subprocess.run(
            [node, _CLI] + [str(a) for a in args],
            cwd=_ARCHIFY_HOME, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "", "archify 超时（%ss）" % timeout
    except OSError as e:
        return False, "", "archify 启动失败：%s" % e
    return p.returncode == 0, (p.stdout or ""), (p.stderr or "")


def _json_block(text):
    """从 CLI 输出里取 JSON（容忍 BOM 与前后杂行）。"""
    raw = (text or "").lstrip("\ufeff").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        i, j = raw.find("{"), raw.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(raw[i:j + 1])
            except json.JSONDecodeError:
                return None
        return None


def _validate_diag(stdout):
    """把 validate --json 输出压成紧凑诊断（issues 截断，防刷屏）。"""
    d = _json_block(stdout)
    if not isinstance(d, dict):
        return {"parsed": False, "raw_tail": (stdout or "")[-600:]}
    comp = d.get("composition") or {}
    summary = comp.get("summary") or {}
    issues = comp.get("issues") or []
    return {
        "parsed": True,
        "ok": bool(d.get("ok")),
        "profile": comp.get("profile"),
        "status": comp.get("status"),
        "errors": summary.get("errors"),
        "warnings": summary.get("warnings"),
        "issue_count": len(issues),
        "issues": issues[:10],
        "metrics": comp.get("metrics") or {},
        "checks": [{"name": c.get("name"), "ok": bool(c.get("ok"))} for c in (d.get("checks") or [])],
    }


def _screenshot(html_path, png_path, width=1800, height=1400, budget=10000):
    """Edge headless 截本地 HTML 为 PNG（免 Playwright/MCP）。返回 (png 或 None, 错误)。"""
    edge = next((p for p in _EDGE_CANDIDATES if os.path.isfile(p)), "")
    if not edge:
        return None, "未找到 Edge（msedge.exe）"
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    try:
        subprocess.run(
            [edge, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--window-size=%d,%d" % (width, height),
             "--virtual-time-budget=%d" % budget,
             "--screenshot=" + os.path.abspath(png_path), url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, "截图失败：%s" % e
    if not os.path.isfile(png_path) or os.path.getsize(png_path) < 1024:
        return None, "截图未产出有效文件"
    return png_path, ""


def _png_check(png_path):
    """用 PIL 验图非空白（三通道 range 不塌缩）。PIL 缺失则返回 None（不阻断）。"""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(png_path) as im:
            w, h = im.size
            extrema = im.convert("RGB").getextrema()
        blank = all(int(lo) == int(hi) for lo, hi in extrema)
        return {"size": [w, h], "blank": blank}
    except Exception:
        return None


@tool(
    "archify_render",
    "把 JSON 图定义编译成单文件交互式 HTML 图（架构图/流程图/时序图/数据流图/生命周期图），"
    "并先用官方 9 项几何校验器（正交/折弯/交叉/标签净空/容器边界/路由节奏）验收。"
    "spec 传 JSON 文本（以 { 开头）或 .json 文件路径；diagram_type 默认 architecture；"
    "quality=showcase 为严格档（0 错 0 警）而 standard 宽松；preview=True 附 PNG 截图。"
    "返回校验诊断（errors/warnings/metrics/issues）与 HTML 产物路径。",
    {
        "diagram_type": {"type": "string", "description": "图类型：architecture(默认)/workflow/sequence/dataflow/lifecycle", "required": False},
        "spec": {"type": "string", "description": "图定义：JSON 文本（以 { 开头）或 .json 文件路径", "required": True},
        "output": {"type": "string", "description": "输出 HTML 路径（可选；默认与 spec 同目录同名 .html；内联 JSON 则落 workspace/tasks/archify_render/）", "required": False},
        "quality": {"type": "string", "description": "校验档位：showcase(默认，严格 0 错 0 警) / standard", "required": False},
        "mode": {"type": "string", "description": "validate=只校验 / render=只渲染 / both(默认，先校验再渲染)", "required": False},
        "preview": {"type": "boolean", "description": "是否附 PNG 截图（Edge headless，默认 False）", "required": False},
        "strict": {"type": "boolean", "description": "严格模式：校验未通过则不渲染（默认 False，仍渲染以便看图）", "required": False},
    },
    group="图形输出",
)
def run(diagram_type="architecture", spec="", output="", quality="showcase",
        mode="both", preview=False, strict=False):
    t = (diagram_type or "architecture").strip().lower()
    if t not in _TYPES:
        return {"ok": False, "error": "diagram_type 必须是 %s 之一" % ", ".join(_TYPES)}
    q = (quality or "showcase").strip().lower()
    if q not in ("showcase", "standard"):
        q = "showcase"
    md = (mode or "both").strip().lower()
    if md not in ("validate", "render", "both"):
        md = "both"
    s = (spec or "").strip()
    if not s:
        return {"ok": False, "error": "spec 不能为空（传 JSON 文本或 .json 路径）"}

    if not _node():
        return {"ok": False, "error": "未找到 node（需要 Node >= 18）"}
    if not os.path.isfile(_CLI):
        return {"ok": False, "error": "archify 依赖缺失：" + _CLI}

    inline = s.startswith("{")
    if inline:
        os.makedirs(_DEFAULT_OUT_DIR, exist_ok=True)
        spec_path = os.path.join(_DEFAULT_OUT_DIR, ".inline_spec.json")
        with open(spec_path, "w", encoding="utf-8", newline="") as f:
            f.write(s)
    else:
        spec_path = os.path.abspath(s)
        if not os.path.isfile(spec_path):
            return {"ok": False, "error": "spec 文件不存在：" + spec_path}

    if output:
        out_html = os.path.abspath(output)
    elif inline:
        out_html = os.path.join(_DEFAULT_OUT_DIR, "%s-%s.html" % (t, time.strftime("%Y%m%d_%H%M%S")))
    else:
        out_html = os.path.splitext(spec_path)[0] + ".html"

    result = {"ok": True, "diagram_type": t, "quality": q, "spec": spec_path, "mode": md}

    # ── 1) 校验（官方 9 项几何检查）
    if md in ("validate", "both"):
        ok_v, so_v, se_v = _run_cli(["validate", t, spec_path, "--json", "--quality", q])
        diag = _validate_diag(so_v)
        diag["cli_ok"] = bool(ok_v)
        if not ok_v and not diag.get("parsed") and se_v:
            diag["stderr_tail"] = se_v[-400:]
        result["validate"] = diag
        result["validated"] = bool(diag.get("ok")) and not diag.get("errors")
        if md == "validate":
            result["html"] = None
            return result
        if strict and not result["validated"]:
            result["ok"] = False
            result["error"] = "严格模式：校验未通过，已中止渲染（看 validate.issues 定位）"
            return result

    # ── 2) 渲染为单文件交互 HTML
    parent = os.path.dirname(out_html)
    if parent:
        os.makedirs(parent, exist_ok=True)
    ok_r, so_r, se_r = _run_cli(["render", t, spec_path, out_html, "--quality", q])
    result["render_ok"] = bool(ok_r) and os.path.isfile(out_html)
    if not result["render_ok"]:
        result["ok"] = False
        result["error"] = (se_r or so_r or "render 失败").strip()[-600:]
        return result
    result["html"] = out_html
    result["html_size"] = os.path.getsize(out_html)

    # ── 3) 可选 PNG 截图（含空白校验）
    if preview:
        png = os.path.splitext(out_html)[0] + ".png"
        got, err = _screenshot(out_html, png)
        if got:
            result["preview"] = got
            chk = _png_check(got)
            if chk:
                result["preview_check"] = chk
        else:
            result["preview_error"] = err

    return result

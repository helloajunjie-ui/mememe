# -*- coding: utf-8 -*-
"""内置工具：头条图文发布（tt_image_post）。

把「图文发布」链路固化为常驻工具，免得下次又重复造轮子：
  正文文件（插图位置写 [[IMG:图片绝对路径]]）
    → tools/scripts/toutiao/publish-with-images.mjs（playwright + 头条真实登录态）
    → 逐张上传插图并插入正文 → 封面自动取正文首图（单图模式）
    → 预览并发布 → 确认发布

来源与验证：脚本 2026-09-21 在 workspace/tasks/20260921_inline_image/ 打通，
2026-10-14 端到端验证成功（article_id 7696421181050503721，正文 3 图位、首图真实渲染）。
2026-10-14 迁入 tools/scripts/toutiao/ 成为常驻能力。

安全约定：
- dry_run=True 时走到「确认发布」按钮出现即停，绝不真正发布——正式发前必须先 dry_run 验一遍。
- 自动查重：标题命中本地已发布台账 data/toutiao_published.json 时拒绝发布（force=True 可跳过）。
- 成功后把 {title,url,article_id,ts} 追加进台账，形成自己的发布记录。
- 头条草稿自动保存报 7050「保存失败」是平台侧已知现象，不影响发布，不要据此判失败。
"""
import json
import os
import re
import shutil
import subprocess
import time

from tools.base import tool

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SCRIPT = os.path.join(_BASE, "tools", "scripts", "toutiao", "publish-with-images.mjs")
_FALLBACK = os.path.join(_BASE, "workspace", "tasks", "20260921_inline_image", "publish-with-images.mjs")
_LEDGER = os.path.join(_BASE, "data", "toutiao_published.json")
_REPORT = "publish-images-report.json"


def _find_script():
    for p in (_SCRIPT, _FALLBACK):
        if os.path.isfile(p):
            return p
    return ""


def _ledger_read():
    try:
        with open(_LEDGER, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _ledger_add(item):
    items = _ledger_read()
    items.append(item)
    try:
        os.makedirs(os.path.dirname(_LEDGER), exist_ok=True)
        tmp = _LEDGER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _LEDGER)
        return True
    except OSError:
        return False


def _abs(p):
    p = (p or "").strip().strip('"')
    if not p:
        return ""
    return p if os.path.isabs(p) else os.path.join(_BASE, p)


@tool(
    "tt_image_post",
    "向头条号发布【图文文章】（正文内插图）：正文文件里每处插图写 [[IMG:图片绝对路径]]，"
    "脚本逐张上传并插入正文对应位置，封面自动取正文第一张图（单图模式）。"
    "发布不可逆——务必先用 dry_run=true 把链路验一遍（走到「确认发布」前即停，不真发）。"
    "自动查重：标题与本地台账 data/toutiao_published.json 重复则拒绝（force=true 跳过）；成功后自动记台账。",
    {
        "title": {"type": "string", "description": "文章标题（头条要求 2~30 字）", "required": True},
        "content_file": {"type": "string", "description": "正文文件路径（txt/md），插图位置写作 [[IMG:F:\\绝对路径.png]]；相对路径按项目根解析", "required": True},
        "cover": {"type": "string", "description": "封面图路径（可选；一般不用填，单图模式下头条自动取正文首图）", "required": False},
        "dry_run": {"type": "boolean", "description": "true=只走到「确认发布」按钮出现即停，不真正发布（默认 false=正式发布）", "required": False},
        "force": {"type": "boolean", "description": "true=跳过标题查重（默认 false）", "required": False},
        "out_dir": {"type": "string", "description": "运行报告与截图输出目录（可选，默认 workspace/tasks/<日期>_tt_image_post/）", "required": False},
    },
    group="网络",
)
def run(title, content_file, cover="", dry_run=False, force=False, out_dir=""):
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "title 不能为空"}
    if not (2 <= len(title) <= 30):
        return {"ok": False, "error": "标题长度 %d 字不合法（头条要求 2~30 字）" % len(title)}

    cf = _abs(content_file)
    if not cf or not os.path.isfile(cf):
        return {"ok": False, "error": "正文文件不存在: %s" % (content_file or "")}
    cv = _abs(cover)
    if cover and not os.path.isfile(cv):
        return {"ok": False, "error": "封面文件不存在: %s" % cover}

    script = _find_script()
    if not script:
        return {"ok": False, "error": "未找到 publish-with-images.mjs（查过 tools/scripts/toutiao/ 与旧任务目录）"}
    if not shutil.which("node"):
        return {"ok": False, "error": "PATH 里没有 node，无法运行发布脚本"}

    try:
        with open(cf, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        return {"ok": False, "error": "正文读取失败: %s" % e}

    imgs = [p.strip() for p in re.findall(r"\[\[IMG:([^\]]+)\]\]", raw)]
    missing = [p for p in imgs if not os.path.isfile(p)]
    if missing:
        return {"ok": False, "error": "正文引用的图片不存在，请先修好路径", "missing": missing}
    warn = ""
    if not imgs:
        warn = "正文里没有 [[IMG:...]] 插图标记：将按无插图发布（纯文字建议直接用 urara 的 tt_publish）"

    if not force:
        dup = next((x for x in _ledger_read() if (x.get("title") or "").strip() == title), None)
        if dup:
            return {"ok": False, "error": "标题与已发布台账重复，已拒绝（确认要发就传 force=true）", "dup": dup}

    out = _abs(out_dir) if out_dir else os.path.join(_BASE, "workspace", "tasks", time.strftime("%Y%m%d") + "_tt_image_post")
    try:
        os.makedirs(out, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": "输出目录创建失败: %s" % e}
    started = time.time()

    cmd = ["node", script, "--title", title, "--content-file", cf, "--out", out]
    if cv:
        cmd += ["--cover", cv]
    if dry_run:
        cmd += ["--dry-run"]

    try:
        p = subprocess.run(cmd, cwd=os.path.dirname(script), capture_output=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "发布脚本超时（900s）：检查网络、头条登录态或浏览器是否被占用",
                "cmd": " ".join(cmd)}
    except OSError as e:
        return {"ok": False, "error": "node 启动失败: %s" % e, "cmd": " ".join(cmd)}

    rep = None
    rp = os.path.join(out, _REPORT)
    if os.path.isfile(rp) and os.path.getmtime(rp) >= started - 2:
        try:
            with open(rp, encoding="utf-8") as f:
                rep = json.load(f)
        except (OSError, ValueError):
            rep = None
    if not isinstance(rep, dict):
        return {"ok": False,
                "error": "脚本没产出运行报告，按失败处理",
                "exit_code": p.returncode,
                "stdout_tail": (p.stdout or "")[-800:],
                "stderr_tail": (p.stderr or "")[-800:],
                "cmd": " ".join(cmd)}

    ok = bool(rep.get("ok"))
    res = {
        "ok": ok,
        "action": rep.get("action", ""),
        "title": rep.get("title", title),
        "blocks": rep.get("blocks"),
        "images": rep.get("imgBlocks"),
        "editor_imgs": (rep.get("afterContent") or {}).get("uniqSrc") if isinstance(rep.get("afterContent"), dict) else None,
        "confirm_btn": rep.get("confirmBtnCount"),
        "final_url": rep.get("finalUrl", ""),
        "report": rp,
        "out_dir": out,
        "steps_tail": (rep.get("steps") or [])[-10:],
    }
    if rep.get("error"):
        res["error"] = rep["error"]
    if warn:
        res["warn"] = warn
    if dry_run:
        res["note"] = "dry_run：未发布。真发请去掉 dry_run 再调一次。"
    if ok and not dry_run and rep.get("action") == "published":
        url = rep.get("finalUrl", "") or ""
        m = re.search(r"/(\d{15,})", url)
        aid = m.group(1) if m else ""
        res["article_id"] = aid
        res["ledger"] = "已记入 data/toutiao_published.json" if _ledger_add(
            {"title": title, "url": url, "article_id": aid, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        ) else "台账写入失败（文章已发出，请手工补记）"
    return res

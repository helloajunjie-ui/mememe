"""内置工具：img_cutout —— 抠图（AI 语义分割去背景，输出透明底 PNG）。

基于 rembg（u2net 语义分割）把图中主体抠出、去掉背景。适合立绘 / 人像 / 商品单体图。

三条硬约束（都是踩坑换来的）：
1. 模型写死 u2net：rembg>=2.0.8x 的 remove() 默认模型已换成 bria-rmbg-2.0
   （1.02GB，且 BRIA RMBG 2.0 为非商用许可 CC BY-NC 4.0）。不能让库的默认值替我们选模型。
   u2net 167MB、本机已缓存、CPU 推理约 0.5s/张、Apache-2.0。
2. 推理跑在独立子进程（sys.executable + 本文件 --worker 入口）：不把 onnxruntime 与模型
   常驻进本体进程，native 崩了也不牵连本体。批量图共用一次子进程，摊薄约 1.6s 冷启动。
   子进程需 PYTHONPATH=项目根，否则 import tools.* 会失败（脚本模式下 sys.path[0] 是脚本目录）。
3. 走 Python API，不走 CLI：rembg.exe 因缺 filetype 包会 ModuleNotFoundError（只有 CLI 分支需要）。

路径边界：读走 fs_explore._check_read_path（项目根 + 只读白名单），写走 _check_path（限项目根内）。
deps 用【导入名】而非 PyPI 名：pillow 的导入名是 PIL，写错会让 tool_health_audit 误报缺失。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tools.base import tool
from tools.src.python.fs_explore import _check_path, _check_read_path

_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
DEFAULT_MODEL = "u2net"
_BG_ALIAS = {
    "green": (0, 177, 64),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
}
_MAX_ITEMS_IN_RETURN = 30


def _collect_sources(src: str, recursive: bool) -> list:
    p = Path(src)
    if p.is_file():
        return [p] if p.suffix.lower() in _IMG_EXT else []
    if p.is_dir():
        it = p.rglob("*") if recursive else p.glob("*")
        return sorted([f for f in it if f.is_file() and f.suffix.lower() in _IMG_EXT])
    return []


def _parse_bg(bg):
    """返回 (r,g,b) 或 None(=透明底)。支持 green/white/#rrggbb/rgb(r,g,b)。"""
    if bg is None:
        return None
    key = str(bg).strip().lower()
    if not key or key in ("transparent", "trans", "none", "alpha"):
        return None
    if key in _BG_ALIAS:
        return _BG_ALIAS[key]
    if key.startswith("#"):
        h = key[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            except ValueError:
                return None
    if key.startswith("rgb(") and key.endswith(")"):
        try:
            parts = [int(x) for x in key[4:-1].split(",")]
            if len(parts) == 3:
                return tuple(max(0, min(255, v)) for v in parts)
        except ValueError:
            return None
    return None


def _worker_main() -> int:
    """子进程入口：stdin 收 JSON 任务，stdout 回一行 JSON 结果（进度条走 stderr）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        print(json.dumps({"ok": False, "fatal": "任务参数解析失败: %s" % e}, ensure_ascii=False))
        return 1

    jobs = payload.get("jobs") or []
    model = payload.get("model") or DEFAULT_MODEL
    alpha_matting = bool(payload.get("alpha_matting"))
    bg = payload.get("bg")
    out = {"model": model, "items": []}

    try:
        from PIL import Image
        from rembg import new_session, remove
    except Exception as e:
        out["fatal"] = "依赖导入失败: %s" % e
        print(json.dumps(out, ensure_ascii=False))
        return 1

    try:
        session = new_session(model)
    except Exception as e:
        out["fatal"] = "模型加载失败(%s): %s" % (model, e)
        print(json.dumps(out, ensure_ascii=False))
        return 1

    kw = {}
    if alpha_matting:
        kw = dict(
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
        )

    for job in jobs:
        rec = {"src": job.get("src"), "out": job.get("out"), "preview": job.get("preview") or None}
        try:
            t0 = time.time()
            with Image.open(job["src"]) as im0:
                im = im0.convert("RGBA")
                rec["size"] = [im.size[0], im.size[1]]
                cut = remove(im, session=session, **kw)
            rec["infer_s"] = round(time.time() - t0, 2)
            cut.save(job["out"])
            try:
                import numpy as np

                a = np.array(cut)[:, :, 3]
                rec["alpha0_ratio"] = round(float((a < 10).mean()), 4)
            except Exception:
                pass
            if rec["preview"] and bg is not None:
                canvas = Image.new("RGBA", cut.size, tuple(bg) + (255,))
                canvas.alpha_composite(cut)
                canvas.convert("RGB").save(rec["preview"], quality=92)
            rec["ok"] = True
        except Exception as e:
            rec["ok"] = False
            rec["error"] = str(e)
        out["items"].append(rec)

    out["ok"] = bool(out["items"]) and all(i.get("ok") for i in out["items"])
    print(json.dumps(out, ensure_ascii=False))
    return 0


@tool(
    "img_cutout",
    "抠图：AI 语义分割去背景，输出透明底 PNG；支持批量/整个目录、可选预览底色与精细边缘（默认模型 u2net）",
    {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "图片文件或目录路径（项目根/只读白名单内）"},
            "out_dir": {"type": "string", "description": "输出目录（项目根内）；默认 workspace/cutouts/<时间戳>/"},
            "model": {"type": "string", "description": "分割模型，默认 u2net；可选 isnet-general-use / bria-rmbg（非商用许可，慎用）"},
            "alpha_matting": {"type": "boolean", "description": "精细边缘（发丝/半透明），默认 false；需 pymatting，慢约十倍以上"},
            "bg": {"type": "string", "description": "预览底色：transparent(默认,不产预览) / green / white / #RRGGBB / rgb(r,g,b)；非透明时额外产 <名>_preview.jpg"},
            "recursive": {"type": "boolean", "description": "src 为目录时是否递归子目录，默认 false"},
            "timeout": {"type": "integer", "description": "子进程超时秒数，默认 600"},
        },
        "required": ["src"],
    },
    deps=["rembg", "onnxruntime", "PIL", "numpy"],
    group="多模态",
)
def run(src, out_dir="", model=DEFAULT_MODEL, alpha_matting=False, bg="transparent",
        recursive=False, timeout=600):
    try:
        src_p = _check_read_path(src)
    except Exception as e:
        return {"ok": False, "error": "路径不可读: %s" % e}

    files = _collect_sources(src_p, bool(recursive))
    if not files:
        return {"ok": False, "error": "没找到图片: %s" % src_p}

    if not out_dir:
        out_dir = "workspace/cutouts/%s" % time.strftime("%Y%m%d_%H%M%S")
    try:
        out_root = Path(_check_path(out_dir))
    except Exception as e:
        return {"ok": False, "error": "输出目录越界（只允许项目根内）: %s" % e}
    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": "创建输出目录失败: %s" % e}

    bg_rgb = _parse_bg(bg)
    want_preview = bg_rgb is not None

    jobs = []
    used = set()
    for f in files:
        stem = f.stem
        name = stem
        i = 2
        while name.lower() in used:
            name = "%s_%d" % (stem, i)
            i += 1
        used.add(name.lower())
        jobs.append({
            "src": str(f),
            "out": str(out_root / (name + "_cut.png")),
            "preview": str(out_root / (name + "_preview.jpg")) if want_preview else "",
        })

    payload = {
        "jobs": jobs,
        "model": model or DEFAULT_MODEL,
        "alpha_matting": bool(alpha_matting),
        "bg": bg_rgb,
    }
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")

    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--worker"],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(root), env=env, timeout=float(timeout or 600),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "抠图子进程超时(>%ss)，未处理完 %d 张" % (timeout, len(jobs))}
    except Exception as e:
        return {"ok": False, "error": "子进程启动失败: %s" % e}
    elapsed = round(time.time() - t0, 2)

    text = (proc.stdout or "").strip()
    if not text:
        return {"ok": False, "error": "子进程无输出", "returncode": proc.returncode,
                "stderr_tail": (proc.stderr or "")[-800:]}
    try:
        res = json.loads(text.splitlines()[-1])
    except Exception as e:
        return {"ok": False, "error": "结果解析失败: %s" % e,
                "stdout_tail": text[-500:], "stderr_tail": (proc.stderr or "")[-500:]}
    if res.get("fatal"):
        return {"ok": False, "error": res["fatal"], "stderr_tail": (proc.stderr or "")[-800:]}

    items = res.get("items") or []
    ok_items = [i for i in items if i.get("ok")]
    fails = [{"src": i.get("src"), "error": i.get("error")} for i in items if not i.get("ok")]
    shown = items[:_MAX_ITEMS_IN_RETURN]
    infer = [i.get("infer_s") for i in ok_items if i.get("infer_s")]
    return {
        "ok": bool(ok_items) and not fails,
        "model": res.get("model") or (model or DEFAULT_MODEL),
        "count": len(items),
        "ok_count": len(ok_items),
        "elapsed_s": elapsed,
        "avg_infer_s": round(sum(infer) / len(infer), 2) if infer else None,
        "out_dir": str(out_root),
        "items": shown,
        "items_truncated": len(items) > len(shown),
        "failures": fails or None,
    }


if __name__ == "__main__":  # 子进程 worker 模式（工具本体走 @tool 注册，不走这里）
    sys.exit(_worker_main())

"""内置工具：video_frames —— 视频抽帧到项目根内 + 生成一张总览拼图。

动机（2026-09-24 用 talkthrough 读自己的介绍片时踩的三个坑）：
1. talkthrough 的 get_frames 只返回"独特帧"（像素去重后），深底白字的卡片彼此太像会被合并——
   22 帧实际只剩 3 帧，OCR 只认出 2 张卡的文字，大半个视频的内容读不到；
2. talkthrough 的帧落在用户目录下的 .talkthrough/jobs/<id>/frames/，在项目根之外，
   而 vision_look 只读项目根内的图片，读不到；
3. get_frames 一次返回多张图会撑爆输出预算（实测 117K 字符被截断），图实际没拿到。

本工具只做三件事，做好：稳定逐帧抽帧（不依赖"独特帧"）→ 落到项目根内 → 拼一张总览图。

v2 改进（同日实测）：总览图里未填充的格子会被视觉模型误读成"视频的空画面"
（22 帧填满 25 格，最后 3 格留白被读成 0:22/0:23/0:24）——
现在给空位画交叉斜线 + 标注 "no frame"，并在顶部加一行元信息，明确这是拼图不是时间轴。

用法铁律（实测得出）：
- 总览图只用于**定位**（哪一秒有什么），文字**必须**回到单帧 vision_look 或源头文件核对；
  实测总览把 "读 · 查 · 写 · 跑工具" 读成 "读 · 写 · 写工具"（漏字），单帧则读对。
- 逐张调 vision_look，不要在一次调用里请求多张 base64（会被输出预算截断）。

只读源视频、只写 workspace/media/ 内，不改动源文件。
边界（如实）：抽帧是离散采样，动态过程（动作/转场）读不到；静音素材没有声音信息。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

from tools.base import tool
from tools.src.python.fs_explore import _check_read_path

_ROOT = Path(__file__).resolve().parents[3]
_MEDIA_ROOT = _ROOT / "workspace" / "media"
_SHEET_MAX_SIDE = 1760


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or ""


def _ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    ff = _ffmpeg()
    if not ff:
        return ""
    cand = Path(ff).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return str(cand) if cand.is_file() else ""


def _probe(video: Path) -> dict:
    fp = _ffprobe()
    if not fp:
        return {}
    try:
        cp = subprocess.run(
            [fp, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(video)],
            capture_output=True, timeout=90)
        data = json.loads(cp.stdout.decode("utf-8", "replace") or "{}")
    except Exception:
        return {}
    info = {}
    fmt = data.get("format") or {}
    try:
        info["duration"] = round(float(fmt.get("duration") or 0), 3)
    except (TypeError, ValueError):
        pass
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            info["width"] = s.get("width")
            info["height"] = s.get("height")
            try:
                num, den = (s.get("avg_frame_rate") or "0/1").split("/")
                if float(den):
                    info["fps"] = round(float(num) / float(den), 3)
            except Exception:
                pass
            break
    if not any(s.get("codec_type") == "audio" for s in data.get("streams") or []):
        info["has_audio"] = False
    return info


def _font(size):
    from PIL import ImageFont
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _make_sheet(frames, labels, out_path, header=""):
    from PIL import Image, ImageDraw
    n = len(frames)
    cols = max(1, min(8, int(math.ceil(math.sqrt(n)))))
    rows = int(math.ceil(n / float(cols)))
    cell_w = max(120, min(360, _SHEET_MAX_SIDE // cols))
    try:
        with Image.open(frames[0]) as im0:
            ratio = (im0.height / float(im0.width)) if im0.width else 0.5625
    except Exception:
        ratio = 0.5625
    cell_h = max(60, int(cell_w * ratio))
    lab_h = 20
    head_h = 26 if header else 0
    W = cols * cell_w
    H = head_h + rows * (cell_h + lab_h)
    canvas = Image.new("RGB", (W, H), (16, 16, 20))
    draw = ImageDraw.Draw(canvas)
    font = _font(14)
    small = _font(13)
    if header:
        try:
            draw.text((8, 6), header, fill=(205, 210, 225), font=font)
        except Exception:
            pass
    # 空位先画交叉斜线并标注，避免被误读成"视频里的空画面"
    for i in range(rows * cols):
        if i < n:
            continue
        r, c = divmod(i, cols)
        x = c * cell_w
        y = head_h + r * (cell_h + lab_h)
        draw.rectangle([x + 1, y + 1, x + cell_w - 2, y + cell_h - 2], fill=(24, 24, 30))
        draw.line([(x + 1, y + 1), (x + cell_w - 2, y + cell_h - 2)], fill=(74, 74, 86), width=2)
        draw.line([(x + cell_w - 2, y + 1), (x + 1, y + cell_h - 2)], fill=(74, 74, 86), width=2)
        try:
            draw.text((x + 8, y + 8), "no frame", fill=(130, 130, 144), font=small)
        except Exception:
            pass
    for i, fp in enumerate(frames):
        r, c = divmod(i, cols)
        x = c * cell_w
        y = head_h + r * (cell_h + lab_h)
        try:
            with Image.open(fp) as im:
                im = im.convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
                canvas.paste(im, (x, y))
        except Exception:
            pass
        try:
            draw.text((x + 5, y + cell_h + 3), labels[i], fill=(235, 235, 235), font=font)
        except Exception:
            pass
    for r in range(rows + 1):
        yy = head_h + r * (cell_h + lab_h)
        draw.line([(0, yy), (W, yy)], fill=(70, 70, 82), width=1)
    for c in range(cols + 1):
        xx = c * cell_w
        draw.line([(xx, 0), (xx, H)], fill=(70, 70, 82), width=1)
    canvas.save(str(out_path), format="JPEG", quality=88)
    return out_path


def _ts(sec: float) -> str:
    s = int(round(sec))
    return "{:d}:{:02d}".format(s // 60, s % 60)


@tool(
    "video_frames",
    "视频抽帧：把视频逐帧抽成项目根内的图片序列，并拼一张带时间戳的总览图。"
    "不依赖 talkthrough 的'独特帧'（相似画面会被去重吞掉，导致内容丢失），"
    "抽出的帧都在项目根内，可直接交给 vision_look 逐帧细看。"
    "注意：总览图只用于定位，具体文字必须回到单帧或源头文件核对（实测总览会漏字）。只读源文件。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "视频文件路径（项目根或只读白名单内，相对或绝对）"},
            "interval": {"type": "number", "description": "抽帧间隔秒，默认 1.0（0.05~60）"},
            "max_frames": {"type": "integer", "description": "最多抽多少帧，默认 40（1~300）"},
            "start": {"type": "number", "description": "起始时间秒，默认 0"},
            "end": {"type": "number", "description": "结束时间秒，不填则到片尾"},
            "width": {"type": "integer", "description": "每帧输出宽度像素，默认 960；0=保持原尺寸"},
            "sheet": {"type": "boolean", "description": "是否拼总览图，默认 true"},
        },
        "required": ["path"],
    },
    group="多模态",
)
def run(path, interval=1.0, max_frames=40, start=0.0, end=None, width=960, sheet=True):
    try:
        src = Path(_check_read_path(path))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "路径不允许: " + str(e)}
    if not src.is_file():
        return {"ok": False, "error": "不是文件: " + str(path)}
    ff = _ffmpeg()
    if not ff:
        return {"ok": False, "error": "未找到 ffmpeg（PATH 和常见路径都没找到）"}

    try:
        iv = float(interval)
    except (TypeError, ValueError):
        iv = 1.0
    iv = max(0.05, min(60.0, iv))
    try:
        n_max = int(max_frames)
    except (TypeError, ValueError):
        n_max = 40
    n_max = max(1, min(300, n_max))
    try:
        t0 = max(0.0, float(start or 0.0))
    except (TypeError, ValueError):
        t0 = 0.0
    t1 = None
    if end is not None:
        try:
            t1 = float(end)
        except (TypeError, ValueError):
            t1 = None
        if t1 is not None and t1 <= t0:
            t1 = None
    try:
        w = int(width)
    except (TypeError, ValueError):
        w = 960

    info = _probe(src)

    try:
        st = src.stat()
        raw = "{}|{}|{}|{}".format(src, st.st_size, st.st_mtime_ns, iv)
        tag = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    except OSError:
        tag = "00000000"
    stem = re.sub(r"[^0-9A-Za-z_-]+", "_", src.stem)[:40] or "video"
    out_dir = _MEDIA_ROOT / (stem + "_" + tag)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": "输出目录创建失败: " + str(e)}

    removed = 0
    for old in out_dir.glob("out_*.jpg"):
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass

    vf = "fps={:.6f}".format(1.0 / iv).rstrip("0").rstrip(".")
    if w and w > 0:
        vf += ",scale={}:-2".format(w)
    cmd = [ff, "-hide_banner", "-nostdin", "-y"]
    if t0 > 0:
        cmd += ["-ss", "{:.3f}".format(t0)]
    cmd += ["-i", str(src)]
    if t1 is not None:
        cmd += ["-t", "{:.3f}".format(t1 - t0)]
    cmd += ["-vf", vf, "-frames:v", str(n_max), "-q:v", "3",
            str(out_dir / "out_%04d.jpg")]
    try:
        cp = subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffmpeg 抽帧超时(>600s)，请缩小范围或加大 interval"}
    except OSError as e:
        return {"ok": False, "error": "ffmpeg 启动失败: " + str(e)}

    frames = sorted(out_dir.glob("out_*.jpg"))
    if not frames:
        tail = cp.stderr.decode("utf-8", "replace")[-600:]
        return {"ok": False, "error": "未抽出任何帧（检查时间范围是否超出片长）",
                "ffmpeg_tail": tail}

    labels = [_ts(t0 + i * iv) for i in range(len(frames))]

    sheet_path = ""
    sheet_err = ""
    if sheet:
        header = "{}  |  {} frames  |  {}s interval  |  {} .. {}".format(
            stem, len(frames), iv, labels[0], labels[-1])
        try:
            sheet_path = str(_make_sheet([str(f) for f in frames], labels,
                                         out_dir / "sheet.jpg", header))
        except Exception as e:  # noqa: BLE001
            sheet_err = str(e)

    res = {
        "ok": True,
        "source": str(src),
        "video": info,
        "dir": str(out_dir),
        "count": len(frames),
        "interval": iv,
        "start": t0,
        "labels": labels,
        "frames": [str(f) for f in frames],
        "cleaned_old": removed,
        "next": "先看 sheet 概览定位，再对关键帧逐张调 vision_look 核字；"
                "总览会漏字，重要文字以单帧或源文件为准；不要一次请求多张 base64",
    }
    if sheet_path:
        res["sheet"] = sheet_path
    elif sheet:
        res["sheet_error"] = sheet_err
    return res

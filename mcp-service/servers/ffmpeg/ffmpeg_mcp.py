# -*- coding: utf-8 -*-
"""FFmpeg MCP Server：把命令参数一大堆的 ffmpeg/ffprobe 封装成语义化工具。

设计理念（共建者）：视频处理属于"命令参数一大堆"类能力，走 MCP 而非裸命令——
素月只需描述要什么（抽帧/转码/裁剪/合并/提取音频/GIF），参数由 server 组装。

工具集（8 个）：
- probe           探测媒体信息（时长/分辨率/码率/流/元数据）
- extract_frames  抽帧（间隔/数量/尺寸）→ 帧图供 vision_look 看（等于给素月一双能看视频的眼睛）
- convert         转码（容器/编码/码率/分辨率/帧率）
- cut             裁剪片段（起止时间）
- merge           拼接多个视频/音频
- extract_audio   提取音频（mp3/m4a/wav）
- make_gif        视频转 GIF
- compress        压缩（按目标码率/CRF/目标大小）

运行方式：stdio MCP server，由 mcp-service 拉起（command=venv python, args=[本文件]）。
ffmpeg/ffprobe 从 PATH 解析（D:\\down\\ffmpeg\\bin 已在 PATH）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ffmpeg")

# 输出目录：受控于 F:\\me\\self-agent\\workspace\\media（素月可预览/引用）
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_OUT_ROOT = os.path.join(_BASE, "workspace", "media")


def _ff() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _fp() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def _run(cmd: list, timeout: int = 600) -> dict:
    """执行命令，返回 {ok, stdout, stderr, output}。"""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"找不到命令: {cmd[0]}（请确认 ffmpeg 已安装并在 PATH）"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"执行超时（>{timeout}s）"}
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return {"ok": False, "error": err[-2000:] or f"退出码 {r.returncode}"}
    return {"ok": True, "stdout": out[-2000:], "stderr": err[-2000:]}


def _out_path(name: str) -> str:
    os.makedirs(_OUT_ROOT, exist_ok=True)
    return os.path.join(_OUT_ROOT, name)


def _check_input(path: str) -> Optional[str]:
    if not path or not os.path.isfile(path):
        return f"输入文件不存在: {path}"
    return None


def _parse_duration(t: str) -> Optional[float]:
    """'HH:MM:SS.mmm' / 'MM:SS' / 'SS' → 秒；失败返回 None。"""
    try:
        parts = [float(p) for p in str(t).split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 1:
            return parts[0]
    except (TypeError, ValueError):
        pass
    return None


@mcp.tool()
def probe(path: str) -> dict:
    """探测媒体文件信息：容器/时长/分辨率/码率/音视频流/元数据。"""
    e = _check_input(path)
    if e:
        return {"ok": False, "error": e}
    return _run([_fp(), "-v", "error", "-show_format", "-show_streams", "-print_format", "json", path])


@mcp.tool()
def extract_frames(path: str, interval: float = 5.0, max_frames: int = 20,
                   scale: Optional[str] = None, start: Optional[float] = None,
                   end: Optional[float] = None) -> dict:
    """按时间间隔抽帧（秒），生成 PNG 序列到 workspace/media/frames_<ts>/，返回帧列表。

    抽帧后可用 vision_look 逐张看图——这是素月"看视频"的唯一通道。
    interval: 抽帧间隔秒（默认 5）；max_frames: 最多帧数（默认 20）；
    scale: 可选缩放如 1280x720；start/end: 可选裁剪时间段（秒）。
    """
    e = _check_input(path)
    if e:
        return {"ok": False, "error": e}
    import datetime
    tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(_OUT_ROOT, f"frames_{tag}")
    os.makedirs(out_dir, exist_ok=True)
    cmd = [_ff(), "-y", "-v", "error", "-i", path]
    if start is not None:
        cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += ["-vf", f"fps=1/{interval}" + (f",scale={scale}" if scale else ""),
            os.path.join(out_dir, "frame_%04d.png")]
    r = _run(cmd)
    if not r["ok"]:
        return r
    frames = sorted(f for f in os.listdir(out_dir) if f.lower().endswith(".png"))
    if not frames:
        return {"ok": False, "error": "未抽到帧（视频太短或 interval 过大？）"}
    frames = frames[:max_frames]
    return {"ok": True, "frames": [os.path.join(out_dir, f) for f in frames],
            "count": len(frames), "dir": out_dir,
            "note": "帧图已生成，可用 vision_look 逐张查看内容"}


@mcp.tool()
def convert(path: str, out_format: str = "mp4", video_codec: Optional[str] = None,
            audio_codec: Optional[str] = None, crf: Optional[int] = None,
            bitrate: Optional[str] = None, resolution: Optional[str] = None,
            fps: Optional[float] = None) -> dict:
    """转码：容器格式（mp4/mkv/webm/mov/mp3/wav 等）、编码（h264/h265/vp9/aac 等）、
    质量（crf 0-51，越小越清晰）、码率（如 2M）、分辨率（如 1280x720）、帧率。"""
    e = _check_input(path)
    if e:
        return {"ok": False, "error": e}
    base = os.path.splitext(os.path.basename(path))[0]
    out = _out_path(f"{base}_conv.{out_format.lstrip('.')}")
    cmd = [_ff(), "-y", "-v", "error", "-i", path]
    if video_codec:
        cmd += ["-c:v", video_codec]
    if crf is not None:
        cmd += ["-crf", str(crf)]
    if bitrate:
        cmd += ["-b:v", bitrate]
    if resolution:
        cmd += ["-vf", f"scale={resolution}"]
    if fps:
        cmd += ["-r", str(fps)]
    if audio_codec:
        cmd += ["-c:a", audio_codec]
    if not video_codec and not audio_codec and out_format.lower() in ("mp3", "wav", "m4a", "aac", "ogg", "flac"):
        cmd += ["-vn"]
    cmd += ["-progress", "pipe:1", out]
    r = _run(cmd)
    if not r["ok"]:
        return r
    return {"ok": True, "output": out, "size": os.path.getsize(out)}


@mcp.tool()
def cut(path: str, start: float, end: Optional[float] = None, duration: Optional[float] = None) -> dict:
    """裁剪片段：start 起始秒；end 结束秒 或 duration 时长（二选一，end 优先）。"""
    e = _check_input(path)
    if e:
        return {"ok": False, "error": e}
    base = os.path.splitext(os.path.basename(path))[0]
    out = _out_path(f"{base}_cut.mp4")
    cmd = [_ff(), "-y", "-v", "error", "-ss", str(start), "-i", path]
    if end is not None:
        cmd += ["-to", str(end)]
    elif duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-c", "copy", out]
    r = _run(cmd)
    if not r["ok"]:
        return r
    return {"ok": True, "output": out, "size": os.path.getsize(out)}


@mcp.tool()
def merge(paths: list) -> dict:
    """拼接多个媒体文件（同格式），按传入顺序合并为一个 mp4。"""
    if not paths or not isinstance(paths, list) or len(paths) < 2:
        return {"ok": False, "error": "至少传 2 个文件路径"}
    for p in paths:
        e = _check_input(p)
        if e:
            return {"ok": False, "error": e}
    import datetime
    tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    list_file = os.path.join(_OUT_ROOT, f"merge_{tag}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in paths:
            f.write(f"file '{p}'\n")
    out = _out_path(f"merged_{tag}.mp4")
    r = _run([_ff(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_file,
              "-c", "copy", out])
    if not r["ok"]:
        return r
    return {"ok": True, "output": out, "size": os.path.getsize(out), "parts": len(paths)}


@mcp.tool()
def extract_audio(path: str, out_format: str = "mp3", bitrate: str = "192k") -> dict:
    """提取音频：mp3/m4a/wav/aac/flac/ogg。"""
    e = _check_input(path)
    if e:
        return {"ok": False, "error": e}
    base = os.path.splitext(os.path.basename(path))[0]
    out = _out_path(f"{base}_audio.{out_format.lstrip('.')}")
    r = _run([_ff(), "-y", "-v", "error", "-i", path, "-vn",
              "-c:a", "libmp3lame" if out_format.lower() == "mp3" else "aac" if out_format.lower() in ("m4a", "aac") else "copy",
              "-b:a", bitrate, out])
    if not r["ok"]:
        return r
    return {"ok": True, "output": out, "size": os.path.getsize(out)}


@mcp.tool()
def make_gif(path: str, start: Optional[float] = None, duration: float = 5.0,
             width: Optional[int] = 480, fps: float = 10.0) -> dict:
    """视频片段转 GIF（默认 480 宽、10fps、5 秒）。"""
    e = _check_input(path)
    if e:
        return {"ok": False, "error": e}
    base = os.path.splitext(os.path.basename(path))[0]
    out = _out_path(f"{base}.gif")
    cmd = [_ff(), "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-t", str(duration), "-i", path,
            "-vf", f"fps={fps},scale={width}:-1:flags=lanczos",
            "-loop", "0", out]
    r = _run(cmd)
    if not r["ok"]:
        return r
    return {"ok": True, "output": out, "size": os.path.getsize(out)}


@mcp.tool()
def compress(path: str, crf: int = 28, preset: str = "medium", resolution: Optional[str] = None,
             audio_bitrate: Optional[str] = "128k") -> dict:
    """压缩视频：crf 越高越小越糊（默认 28）；可选分辨率缩放；输出 mp4。"""
    e = _check_input(path)
    if e:
        return {"ok": False, "error": e}
    base = os.path.splitext(os.path.basename(path))[0]
    out = _out_path(f"{base}_compressed.mp4")
    cmd = [_ff(), "-y", "-v", "error", "-i", path, "-c:v", "libx264",
           "-crf", str(crf), "-preset", preset]
    if resolution:
        cmd += ["-vf", f"scale={resolution}"]
    if audio_bitrate:
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", out]
    r = _run(cmd)
    if not r["ok"]:
        return r
    return {"ok": True, "output": out, "size": os.path.getsize(out),
            "saved_bytes": os.path.getsize(path) - os.path.getsize(out)}


if __name__ == "__main__":
    mcp.run(transport="stdio")

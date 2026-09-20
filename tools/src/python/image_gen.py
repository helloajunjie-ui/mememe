# -*- coding: utf-8 -*-
"""内置工具：豆包 Seedream 生图（image_gen）。

从乌拉拉项目移植（原 Go 实现）→ Python 版：
- 火山方舟 Ark images/generations（OpenAI 兼容），模型 doubao-seedream-4-5-251128。
- 按张计费；返回本地图片路径（png），可配图/封面/视觉素材。
- key 从 .env 的 ARK_API_KEY 读取（已配置）。
"""
import datetime
import json
import os
import re
import urllib.request

import requests

from tools.base import tool

_ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
_DEFAULT_MODEL = "doubao-seedream-4-5-251128"
_SIZES = {"1920x1920", "2048x2048", "2400x1536", "1536x2400", "2560x1440", "1440x2560"}
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read_key() -> str:
    k = os.environ.get("ARK_API_KEY", "")
    if k:
        return k.strip()
    env_path = os.path.join(_BASE, ".env")
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ARK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


@tool(
    "image_gen",
    "用豆包 Seedream 生成图片（按张计费）：适合配图/封面/视觉素材/示意图。"
    "prompt 写主题+风格+构图；可选 size 控制比例；可选 model 指定模型（默认 doubao-seedream-4-5-251128）。"
    "生成后返回本地图片路径，保存到当前任务目录（workspace/tasks/日期_主题/），可预览。",
    {
        "prompt": {"type": "string", "description": "图片描述：主题 + 风格 + 构图（如：古风庭院里一位女子正在研墨，暖色调，柔和光线，横版构图）", "required": True},
        "size": {"type": "string", "description": "图片尺寸（Seedream 要求 ≥3686400 像素）：1920x1920(默认方图)/2048x2048/2400x1536(横版)/1536x2400(竖版)/2560x1440/1440x2560", "required": False},
        "model": {"type": "string", "description": "模型名（可选，默认 doubao-seedream-4-5-251128）", "required": False},
    },
)
def run(prompt: str, size: str = "1920x1920", model: str = "") -> dict:
    key = _read_key()
    if not key:
        return {"ok": False, "error": "缺少 ARK_API_KEY（.env 未配置）"}
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "prompt 不能为空"}
    size = (size or "1920x1920").strip()
    if size not in _SIZES:
        size = "1920x1920"
    model = (model or _DEFAULT_MODEL).strip()

    try:
        resp = requests.post(
            _ARK_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "prompt": prompt.strip(), "size": size, "response_format": "url"},
            timeout=120,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"请求失败: {e}"}

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:300])
        except ValueError:
            detail = resp.text[:300]
        return {"ok": False, "error": f"API {resp.status_code}: {detail}"}

    try:
        data = resp.json().get("data") or []
        if not data:
            return {"ok": False, "error": "API 返回空 data"}
        url = data[0].get("url", "")
        if not url:
            b64 = data[0].get("b64_json", "")
            if not b64:
                return {"ok": False, "error": "API 未返回 url/b64_json"}
            import base64
            raw = base64.b64decode(b64)
            return _save(raw, prompt, size)
        # 下载 url
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
        return _save(raw, prompt, size)
    except Exception as e:
        return {"ok": False, "error": f"解析/下载失败: {e}"}


def _save(raw: bytes, prompt: str, size: str) -> dict:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = re.sub(r"[^\w\u4e00-\u9fff]+", "_", prompt.strip())[:20] or "img"
    d = datetime.date.today().strftime("%Y%m%d")
    out_dir = os.path.join(_BASE, "workspace", "tasks", f"{d}_imagegen")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"gen_{ts}_{tag}_{size}.png")
    with open(path, "wb") as f:
        f.write(raw)
    return {
        "ok": True,
        "image_path": path,
        "size": size,
        "bytes": len(raw),
        "model": _DEFAULT_MODEL,
        "note": "图片已保存到任务目录，可用 fs_read 或 webui 预览；如需配文可继续处理",
    }

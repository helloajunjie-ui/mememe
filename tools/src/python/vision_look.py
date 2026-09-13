"""内置工具：vision_look —— 读取本地图片（让白绫真正\"看见\"图像内容）。

用途：persona 设定图、截图、照片、图表等本地图片的视觉理解。
实现：PIL 压缩 → base64 → OpenAI 兼容多模态接口（chat/completions，content 数组）。
注意：thinking 模型会把 max_tokens 用于思考链，正文可能为空——content 空且有
reasoning_content 时自动加倍额度重试一次（这是首轮探测踩过的坑）。
"""
from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path

from tools.base import tool

_ROOT = Path(__file__).resolve().parents[3]
_LLM_JSON = _ROOT / "config" / "llm.json"
_ENV_FILE = _ROOT / ".env"
_DEFAULT_PROMPT = ("如实描述这张图：画面主体、人物或物体外观（颜色/材质/款式/细节）、"
                   "整体配色、构图与氛围。只写真实看到的内容，不确定就说不确定，不要脑补。")
_MAX_SIDE = 1280


def _api_key() -> str:
    try:
        cfg = json.loads(_LLM_JSON.read_text(encoding="utf-8"))
        k = (cfg.get("api_key") or "").strip()
        if k:
            return k
    except Exception:  # noqa: BLE001
        pass
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("BAILING_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _endpoint() -> tuple:
    base, model = "https://api.deepseek.com", "deepseek-v4-flash"
    try:
        cfg = json.loads(_LLM_JSON.read_text(encoding="utf-8"))
        base = cfg.get("base_url") or base
        model = cfg.get("model") or model
    except Exception:  # noqa: BLE001
        pass
    return base, model


def _encode(path: Path, max_side: int) -> tuple:
    from PIL import Image

    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    w, h = im.size
    scale = max_side / max(w, h)
    if scale < 1:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    data = buf.getvalue()
    return base64.b64encode(data).decode("utf-8"), (im.size[0], im.size[1]), len(data)


@tool(
    "vision_look",
    "看图：读取本地图片并返回视觉描述（人物外观/物体/配色/构图/氛围）。"
    "适用于设定图、截图、照片、图表等图像理解任务；只读项目根目录内的图片。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "图片路径（项目根目录内，相对或绝对），如 persona/concept/x.png"},
            "question": {"type": "string",
                         "description": "可选，针对图的具体提问；默认做全面如实描述"},
            "max_side": {"type": "number",
                         "description": "发送前缩放长边像素，默认 1280（越大越清晰但更慢更贵）"},
            "model": {"type": "string",
                      "description": "可选，覆盖视觉模型（默认用 config/llm.json 的当前模型）"},
            "max_tokens": {"type": "number",
                           "description": "输出上限，默认 2000（thinking 模型会占用部分额度）"},
        },
        "required": ["path"],
    },
)
def run(path: str, question: str = "", max_side: int = _MAX_SIDE,
        model: str = "", max_tokens: int = 2000) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = _ROOT / path
    try:
        p = p.resolve()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"路径解析失败: {e}"}
    if p != _ROOT and _ROOT not in p.parents:
        return {"ok": False, "error": "只允许读取项目根目录内的图片（防越权读取）"}
    if not p.is_file():
        return {"ok": False, "error": f"图片不存在: {p}"}

    key = _api_key()
    if not key:
        return {"ok": False, "error": "未找到 API key（.env / config/llm.json）"}
    base, default_model = _endpoint()
    use_model = model.strip() or default_model

    try:
        b64, size, sent = _encode(p, int(max_side))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"图片读取/编码失败: {type(e).__name__}: {e}"}

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, base_url=base, timeout=240)
        prompt = (question or "").strip() or _DEFAULT_PROMPT
        t0 = time.time()
        attempts = []
        content, finish, usage = "", "", {}
        budget = int(max_tokens)
        r = None
        for _ in range(2):
            r = client.chat.completions.create(
                model=use_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }],
                max_tokens=budget,
            )
            msg = r.choices[0].message
            content = (msg.content or "").strip()
            rlen = len(getattr(msg, "reasoning_content", "") or "")
            finish = r.choices[0].finish_reason
            usage = {"in": r.usage.prompt_tokens, "out": r.usage.completion_tokens}
            attempts.append({"max_tokens": budget, "content_len": len(content),
                             "reasoning_len": rlen, "finish": finish})
            if content:
                break
            budget *= 2
        elapsed = round(time.time() - t0, 1)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"视觉调用失败: {type(e).__name__}: {str(e)[:500]}"}

    if not content:
        return {"ok": False, "error": "模型未返回正文（可能额度不足或模型不支持图像输入）",
                "model": use_model, "attempts": attempts}

    return {
        "ok": True,
        "path": str(p),
        "model": use_model,
        "model_echo": getattr(r, "model", ""),
        "image": {"size": list(size), "sent_bytes": sent},
        "elapsed_s": elapsed,
        "usage": usage,
        "attempts": attempts,
        "description": content,
    }

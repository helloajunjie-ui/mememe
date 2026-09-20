# -*- coding: utf-8 -*-
"""内置工具：二维码生成与解析（qr_gen / qr_read）。

- qr_gen：文本/URL → PNG 二维码，保存到当前任务目录（workspace/tasks/日期_qr/）。
  用于把信息（链接、配置、凭证提示、任务标识等）变成可扫码传递的载体。
- qr_read：图片路径 → 解码出二维码内容。用于读取外部传入的二维码图片
  （截图、拍照、网图），把信息提取回文本。
依赖：qrcode（生成）、opencv-python-headless（解析，QRCodeDetector）。
"""
import datetime
import os
import re

from tools.base import tool

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _out_dir(tag: str) -> str:
    d = datetime.date.today().strftime("%Y%m%d")
    out_dir = os.path.join(_BASE, "workspace", "tasks", f"{d}_{tag}")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


@tool(
    "qr_gen",
    "生成二维码图片：把文本/链接/短信息变成可扫码的 PNG 图片，保存到任务目录并返回路径。"
    "适合传递链接、配置、口令等需要扫码带走的信息。",
    {
        "content": {"type": "string", "description": "要编码进二维码的内容（URL/文本/配置片段等）", "required": True},
        "size": {"type": "integer", "description": "二维码图片边长像素，默认 400（越大越清晰，扫描距离越远）", "required": False},
    },
)
def gen(content: str, size: int = 400) -> dict:
    import qrcode

    if not content or not str(content).strip():
        return {"ok": False, "error": "content 不能为空"}
    content = str(content)
    size = int(size or 400)
    if size < 100 or size > 4000:
        size = 400

    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(content)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        img = img.resize((size, size))
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = re.sub(r"[^\w\u4e00-\u9fff]+", "_", content)[:20] or "qr"
        out_dir = _out_dir("qr")
        path = os.path.join(out_dir, f"qr_{ts}_{tag}.png")
        img.save(path)
        return {"ok": True, "image_path": path, "size": size,
                "content_len": len(content),
                "note": "二维码已保存，可用 fs_read 预览或直接发送给用户扫码"}
    except Exception as e:
        return {"ok": False, "error": f"生成失败: {e}"}


@tool(
    "qr_read",
    "解析二维码图片：传入图片路径（截图/拍照/网图），解码出二维码里的文本内容。"
    "用于把外部传来的二维码还原成信息（链接/配置/口令等）。",
    {
        "image_path": {"type": "string", "description": "二维码图片的本地路径（png/jpg 等）", "required": True},
    },
)
def read(image_path: str) -> dict:
    if not image_path or not os.path.isfile(image_path):
        return {"ok": False, "error": f"图片不存在: {image_path}"}
    try:
        import cv2
        import numpy as np
        # cv2.imread 不支持非 ASCII 路径（中文），用 np.fromfile + imdecode 读
        raw = np.fromfile(image_path, dtype=np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if img is None:
            return {"ok": False, "error": "无法读取图片（可能不是有效图像文件）"}
        detector = cv2.QRCodeDetector()
        data, points, _ = detector.detectAndDecode(img)
        if not data:
            return {"ok": False, "error": "图片中未识别到二维码（可能模糊/过小/非二维码）"}
        return {"ok": True, "content": data, "content_len": len(data),
                "note": "解析成功，content 即二维码承载的信息"}
    except Exception as e:
        return {"ok": False, "error": f"解析失败: {e}"}

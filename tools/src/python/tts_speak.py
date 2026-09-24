"""内置工具：tts_speak —— 文字转语音（edge-tts 神经语音，免密钥、不用申请 API key）。

动机（2026-09-24 实测）："从文字到声音"一直是我的缺口。edge-tts 其实早装在本机（7.0.2），
但它一跑就报错，报的还像是"网络不通"，所以一直没人用。实测两个坑，本工具都已修掉：

坑1 DNS 假阳性：本机系统 DNS 被代理接管（nslookup speech.platform.bing.com → 198.18.1.43，
     典型 fake-ip 虚拟地址，DNS 服务器写 198.18.0.2）。
     aiohttp 装了 aiodns 时会默认走 c-ares，绕过系统解析器 → "Could not contact DNS servers"，
     看起来像被墙，其实不是。修法：给 Communicate / list_voices 传一个带 ThreadedResolver
     的 connector（走系统解析），只作用于本次调用、不打全局补丁，免得影响其他 aiohttp 工具。

坑2 403：7.0.2 的 WSS 握手被拒，是微软改过 Sec-MS-GEC 令牌算法、旧版算不对；
     升级到 7.2.8 即通（时钟偏移也会 403，本机实测只差 4 秒，已排除）。

实测闭环：合成 10.08s 中文 mp3 → 交给 talkthrough 转写 → 现代白话逐字还原
（"我是素月，这句话是我自己说的，从文字到声音，第一次"）。
注意：我"听"回来靠 whisper，它处理文言/古诗会串字（实测"明河共影"被读成"銘和共影"），
那是 STT 的锅、不是合成的锅——别拿转写文本判断音质。

能力边界（如实）：依赖微软消费级接口（非官方，可能再变）；需要外网；
无情感/多角色对白控制；不生成背景音乐；输出固定 MP3。
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from tools.base import tool

_ROOT = Path(__file__).resolve().parents[3]
_OUT_DIR = _ROOT / "workspace" / "media" / "tts"

_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def _connector():
    """带 ThreadedResolver 的 aiohttp connector（本机 fake-ip 代理环境必需）。
    只在单次调用内使用，不改 aiohttp 全局默认解析器。"""
    import aiohttp
    from aiohttp.resolver import ThreadedResolver
    return aiohttp.TCPConnector(resolver=ThreadedResolver())


def _safe_name(name: str) -> str:
    name = os.path.basename((name or "").strip())
    if not name:
        return ""
    stem = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]", "_", os.path.splitext(name)[0]).strip("_")
    return (stem or "tts") + ".mp3"


def _ffprobe() -> str:
    import shutil
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    ff = shutil.which("ffmpeg")
    if not ff:
        return ""
    cand = Path(ff).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return str(cand) if cand.is_file() else ""


def _duration(path: Path) -> float:
    exe = _ffprobe()
    if not exe:
        return 0.0
    try:
        cp = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, timeout=30)
        return round(float((cp.stdout or b"").decode("utf-8", "replace").strip() or 0), 3)
    except Exception:
        return 0.0


def _run_async(coro, timeout=220):
    """在同步工具里跑协程：无事件循环则 asyncio.run；已有循环则丢到独立线程。"""
    box = {}

    def _worker():
        try:
            box["v"] = asyncio.run(asyncio.wait_for(coro, timeout))
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if in_loop:
        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        th.join(timeout + 30)
    else:
        _worker()

    if "e" in box:
        raise box["e"]
    if "v" not in box:
        raise TimeoutError("协程未在预期时间内返回")
    return box["v"]


async def _say(text, voice, rate, volume, pitch, out_path):
    import edge_tts
    conn = _connector()
    try:
        kw = {"connector": conn}
        try:
            r = float(rate)
        except (TypeError, ValueError):
            r = 1.0
        if abs(r - 1.0) > 1e-6:
            kw["rate"] = "{:+d}%".format(int(round((r - 1.0) * 100)))
        try:
            vol = float(volume)
        except (TypeError, ValueError):
            vol = 1.0
        if abs(vol - 1.0) > 1e-6:
            kw["volume"] = "{:+d}%".format(int(round((vol - 1.0) * 100)))
        try:
            p = int(pitch or 0)
        except (TypeError, ValueError):
            p = 0
        if p:
            kw["pitch"] = "{:+d}Hz".format(max(-100, min(100, p)))
        await edge_tts.Communicate(text, voice, **kw).save(str(out_path))
    finally:
        try:
            await conn.close()
        except Exception:
            pass


async def _voices():
    import edge_tts
    conn = _connector()
    try:
        return await edge_tts.list_voices(connector=conn)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


def _hint(msg: str) -> str:
    if "403" in msg:
        return "（403：多为 edge-tts 版本落后于微软令牌算法，试 `pip install -U edge-tts`；也可能出口 IP 被拒）"
    if "DNS" in msg or "getaddrinfo" in msg or "resolver" in msg or "c-ares" in msg:
        return "（DNS 解析失败：本工具已内置 ThreadedResolver，若仍失败请检查代理是否在跑）"
    return ""


@tool(
    "tts_speak",
    "文字转语音：用 edge-tts 神经语音把文本合成 MP3（免密钥），落在 workspace/media/tts/，"
    "可直接给视频配音。action='list' 可查可用嗓音（含多个中文嗓音）。"
    "内置修好本机 fake-ip 代理导致的 DNS 解析失败与旧版 403 两个坑。只写 workspace/media。",
    {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要合成的文本（action='say' 时必填，上限 20000 字符）"},
            "voice": {"type": "string", "description": "嗓音 ShortName，默认 zh-CN-XiaoxiaoNeural（女声）；男声可用 zh-CN-YunyangNeural"},
            "rate": {"type": "number", "description": "语速倍率，默认 1.0（0.5~2.0）"},
            "volume": {"type": "number", "description": "音量倍率，默认 1.0（0.5~2.0）"},
            "pitch": {"type": "integer", "description": "音调偏移 Hz，默认 0（-100~100）"},
            "out_name": {"type": "string", "description": "输出文件名，默认按时间戳自动生成"},
            "action": {"type": "string", "description": "say=合成语音（默认）/ list=列出可用嗓音"},
            "voice_filter": {"type": "string", "description": "action='list' 时的过滤词，如 zh、en-US、Yun"},
        },
    },
    group="多模态",
)
def run(text="", voice=_DEFAULT_VOICE, rate=1.0, volume=1.0, pitch=0,
        out_name="", action="say", voice_filter=""):
    try:
        import edge_tts  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "edge-tts 未安装：" + str(e)}

    act = (action or "say").strip().lower()

    if act == "list":
        try:
            vs = _run_async(_voices(), timeout=60)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            return {"ok": False,
                    "error": "列嗓音失败：{}: {}{}".format(type(e).__name__, msg[:300], _hint(msg))}
        kw = (voice_filter or "").strip().lower()
        rows = []
        for v in vs:
            short = v.get("ShortName") or ""
            loc = v.get("Locale") or ""
            if kw and kw not in (short + " " + loc).lower():
                continue
            rows.append({"voice": short, "locale": loc, "gender": v.get("Gender")})
        return {"ok": True, "total": len(vs), "count": len(rows), "voices": rows[:200]}

    txt = (text or "").strip()
    if not txt:
        return {"ok": False, "error": "text 为空（action='say' 需要文本）"}
    if len(txt) > 20000:
        return {"ok": False, "error": "文本过长（{} 字符 > 20000），请分段合成".format(len(txt))}

    v = (voice or _DEFAULT_VOICE).strip() or _DEFAULT_VOICE
    name = _safe_name(out_name) or "tts_{}_{}.mp3".format(
        time.strftime("%Y%m%d_%H%M%S"), v.split("-")[-1].replace("Neural", ""))
    out_path = _OUT_DIR / name
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": "创建输出目录失败：" + str(e)}

    t0 = time.time()
    try:
        _run_async(_say(txt, v, rate, volume, pitch, out_path), timeout=220)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        return {"ok": False,
                "error": "合成失败：{}: {}{}".format(type(e).__name__, msg[:300], _hint(msg))}

    if not out_path.is_file() or out_path.stat().st_size < 512:
        return {"ok": False, "error": "合成未产出有效文件：" + str(out_path)}

    return {
        "ok": True,
        "voice": v,
        "path": str(out_path),
        "rel_path": "workspace/media/tts/" + name,
        "size": out_path.stat().st_size,
        "duration_s": _duration(out_path),
        "chars": len(txt),
        "elapsed_s": round(time.time() - t0, 2),
        "note": "MP3 在项目根内，可直接给视频配音，或交给 talkthrough 转写自检。",
    }

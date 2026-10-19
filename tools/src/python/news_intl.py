"""国外 AI 一手资讯（本地 getnews 聚合服务）"""
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.base import tool

BASE = "http://127.0.0.1:8788"
EXE = r"F:\GETNEWS\getnews.exe"
PROXY = "127.0.0.1:7897"

# 本地地址必须绕过系统代理，否则会被转发到 7897 报 502
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(path, timeout=20):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "suyue-news-intl"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _alive():
    try:
        _get("/api/sources", timeout=3)
        return True
    except Exception:
        return False


def _ensure_service():
    """服务没起就后台拉起（不阻塞主流程，最多等 8 秒）"""
    if _alive():
        return "running"
    if not Path(EXE).exists():
        return "missing_exe"
    try:
        subprocess.Popen(
            [EXE, "--listen", ":8788", "--proxy", PROXY],
            cwd=str(Path(EXE).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        )
    except Exception as e:
        return "launch_failed:%s" % e
    for _ in range(16):
        time.sleep(0.5)
        if _alive():
            return "started"
    return "start_timeout"


def _parse_ts(s):
    try:
        return datetime.strptime((s or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


@tool(
    "news_intl",
    "查国外 AI 一手资讯：本地 getnews 服务聚合 30+ 家海外源（OpenAI/Anthropic/DeepMind/Mistral/Cohere/HuggingFace/arXiv/媒体等）的官网更新，按时间倒序返回。服务没启动会自动拉起。",
    {
        "limit": {"type": "integer", "description": "返回条数上限 1~200，默认 30", "required": False},
        "sources": {"type": "string", "description": "只保留这些源 key，逗号分隔（如 anthropic,openai,mistral）；空=全部", "required": False},
        "hours": {"type": "integer", "description": "只看最近 N 小时，0=不限（默认 0）", "required": False},
        "refresh": {"type": "boolean", "description": "True 时先触发一次后台抓取（异步，不等待完成）", "required": False},
    },
)
def run(limit: int = 30, sources: str = "", hours: int = 0, refresh: bool = False) -> str:
    st = _ensure_service()
    if st.startswith("missing_exe") or st.startswith("launch_failed") or st == "start_timeout":
        return "[news_intl] 服务不可用: %s（预期可执行文件 %s）" % (st, EXE)

    note = ""
    if refresh:
        try:
            _get("/api/refresh", timeout=10)
            note = "（已触发后台抓取，约 2 分钟后可再查）"
        except Exception as e:
            note = "（刷新触发失败: %s）" % e

    limit = max(1, min(int(limit or 30), 200))
    data = _get("/api/items?limit=200", timeout=60)
    items = data.get("data") or []

    want = {s.strip() for s in (sources or "").split(",") if s.strip()}
    if want:
        items = [d for d in items if d.get("source") in want]

    if hours and hours > 0:
        cut = datetime.now(timezone.utc) - timedelta(hours=hours)
        items = [d for d in items if (_parse_ts(d.get("published_at")) or cut) >= cut]

    items = items[:limit]
    if not items:
        return "[news_intl] 无匹配条目（库存总数 %s）%s" % (data.get("total"), note)

    lines = ["[news_intl] %d 条 / 库存 %s（服务:%s）%s" % (len(items), data.get("total"), st, note)]
    for d in items:
        ts = (d.get("published_at") or "")[:16].replace("T", " ")
        lines.append("· [%s] %s | %s" % (ts, d.get("source_name") or d.get("source"), d.get("title", "")))
        if d.get("url"):
            lines.append("  %s" % d["url"])
    return "\n".join(lines)

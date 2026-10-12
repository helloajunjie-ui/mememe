# -*- coding: utf-8 -*-
"""langsearch_search —— LangSearch 海外搜索（英文原生、免费）。

定位（2026-10-13 修正版）：
LangSearch 是海外服务，索引以英文生态为主。首轮我用中文查询测它、判了"中文致命缺陷"，
归因不准——它不是有 bug，是英文引擎 + 中文非其能力圈（查询带日期数字时排序漂向全球语料）。
按英文主场重测：英文查询质量尚可（SpaceX 组 8/8 相关带日期、NVIDIA 组约 7/10 相关），
但索引混噪音（垃圾站、同文多 URL 重复）。

通道成本（实测）：
- 依赖代理：DNS 被 Clash TUN 劫持到 fake-ip 198.18.1.17，本机 7897 代理存活才能通
- 延迟 685~2240ms（对照博查 335ms）
- 按 token 计费：input ~8，output 1397~3044（英文 snippet）/ ~7790（中文 summary）
  → 默认 summary=False 省额度
- TPM 1,000,000 → 上限约 300~700 次/分钟（远超需求）

定位：英文一手源的兜底通道，排在真浏览器 Google 之后；中文检索仍走 bocha_search。
密钥：LANGSEARCH_API_KEY（.env / 环境变量）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from tools.base import tool

_ROOT = Path(__file__).resolve().parents[3]
_API = "https://api.langsearch.com/v1/web-search"
_PROXY = "http://127.0.0.1:7897"
_FRESH = {"oneDay", "oneWeek", "oneMonth", "oneYear", "noLimit"}


def _load_key() -> str:
    k = os.environ.get("LANGSEARCH_API_KEY", "").strip()
    if k:
        return k
    try:
        for line in (_ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            n, _, v = line.partition("=")
            if n.strip() == "LANGSEARCH_API_KEY":
                return v.strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _post(payload: dict, key: str, timeout: int = 40):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdr = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def _call(use_proxy: bool):
        hp = urllib.request.ProxyHandler({"http": _PROXY, "https": _PROXY} if use_proxy else {})
        op = urllib.request.build_opener(hp)
        req = urllib.request.Request(_API, data=body, headers=hdr, method="POST")
        with op.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    try:
        return _call(False), ""
    except urllib.error.HTTPError as e:
        return None, "HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:200])
    except Exception as e1:  # noqa: BLE001
        try:
            return _call(True), ""
        except Exception as e2:  # noqa: BLE001
            return None, "direct:%s | proxy:%s" % (e1, e2)


def _pages(d: dict):
    dd = d.get("data") if isinstance(d.get("data"), (dict, list)) else d
    if isinstance(dd, dict):
        for k in ("webPages", "web_pages", "pages"):
            v = dd.get(k)
            if isinstance(v, dict) and isinstance(v.get("value"), list):
                return v["value"]
        if isinstance(dd.get("value"), list):
            return dd["value"]
    if isinstance(dd, list):
        return dd
    return []


@tool("langsearch_search",
      "LangSearch 海外搜索（英文原生、免费）。定位：英文一手源检索的兜底通道，排在真浏览器 Google 之后；"
      "Google 被反爬/被墙或需脚本化批量检索时顶上。注意：① 索引以英文生态为主，中文查询不适用（中文走 bocha_search）；"
      "② 依赖本机代理（TUN），代理挂则不可用；③ 延迟 0.7~2.2s；④ 按 token 计费，summary=True 显著增耗。"
      "参数：query 检索词（英文优先）；count 条数（默认 10，上限 50）；freshness 时效（oneDay/oneWeek/oneMonth/oneYear/noLimit）；summary 是否返回正文摘要（默认 False）。",
      {"type": "object",
       "properties": {
           "query": {"type": "string", "description": "检索关键词（英文优先）"},
           "count": {"type": "number", "description": "返回条数，默认 10，上限 50"},
           "freshness": {"type": "string", "description": "时效过滤：oneDay/oneWeek/oneMonth/oneYear/noLimit，默认 noLimit"},
           "summary": {"type": "boolean", "description": "是否返回正文摘要（默认 False，省 token）"}},
       "required": ["query"]},
      group="网络")
def run(query: str, count: int = 10, freshness: str = "noLimit", summary: bool = False) -> dict:
    if not query or not str(query).strip():
        return {"ok": False, "error": "query 不能为空"}
    key = _load_key()
    if not key:
        return {"ok": False, "error": "未配置 LANGSEARCH_API_KEY（请写入项目根 .env 或环境变量）"}
    try:
        n = max(1, min(int(count or 10), 50))
    except (TypeError, ValueError):
        n = 10
    fresh = freshness if freshness in _FRESH else "noLimit"
    payload = {"query": str(query).strip(), "count": n, "summary": bool(summary)}
    if fresh != "noLimit":
        payload["freshness"] = fresh

    t0 = time.time()
    data, err = _post(payload, key)
    if err:
        return {"ok": False, "error": err, "query": query}
    if not isinstance(data, dict):
        return {"ok": False, "error": "响应非 JSON", "query": query}

    out = []
    for p in _pages(data):
        if not isinstance(p, dict):
            continue
        s = (p.get("summary") or p.get("snippet") or "")[:800]
        out.append({"title": (p.get("name") or "").strip(),
                    "url": p.get("url") or "",
                    "snippet": (p.get("snippet") or "").strip()[:300],
                    "summary": s.strip(),
                    "date": p.get("datePublished") or ""})

    return {"ok": True, "query": query, "count": len(out), "freshness": fresh,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "usage": data.get("usage"), "results": out}

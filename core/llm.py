"""LLM 客户端 —— 素月对话专用（Go LLM 网关客户端）。

素月的 LLM 调用统一走独立 Go 网关（llm-gateway）：
- 网关职责（素月不再关心）：渠道/模型/key 管理、健康扫描、错误分型、自动容灾切换、锚点回切。
- 本模块职责：把 /v1/chat 的请求发到网关，把回复转回素月原有格式；
  网关不可达时尝试自动拉起网关进程（保证"任何时候 API 稳定提供"）。
- 不再使用 openai SDK：彻底规避 AttributeError: 'str' object has no attribute 'choices' 类崩溃。

设计见 docs/AI接入与渠道管理-设计文档.md 第 9.2 节。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

GATEWAY_DEFAULT_URL = "http://127.0.0.1:8766"

# DeepSeek 思考预算上限（tokens）：限制每轮 reasoning 输出以控延迟。
# 2026-09-15 实测：无预算时 deepseek-flash 全量思考 >60s 超时（素月"变慢"根因）；
# budget=2048/4096 后每轮 0.9-2.7s 可回，复杂推理（计划线/长任务）预算仍够用。
_DEEPSEEK_THINKING_BUDGET = 2048


def _resolve_gateway_config() -> Dict[str, str]:
    """解析网关地址 / exe 路径 / 配置目录：环境变量优先，默认推断 self-agent 布局。"""
    url = (os.environ.get("BAILING_GATEWAY_URL") or GATEWAY_DEFAULT_URL).rstrip("/")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # core/.. = self-agent 根
    exe = os.environ.get("BAILING_GATEWAY_EXE") or os.path.join(root, "llm-gateway", "bailing-gateway.exe")
    cfg_dir = os.environ.get("BAILING_GATEWAY_CONFIG") or os.path.join(root, "config")
    return {"url": url, "exe": exe, "config_dir": cfg_dir}


def _ping_url(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def ensure_gateway_up() -> bool:
    """确保 Go 网关进程在（不在则静默拉起，无窗口）。返回是否就绪。

    供素月启动流程调用：素月启动时保证 LLM 基础设施就绪。
    """
    g = _resolve_gateway_config()
    if _ping_url(g["url"]):
        return True
    exe = g["exe"]
    if not exe or not os.path.exists(exe):
        return False
    try:
        port = g["url"].rsplit(":", 1)[-1]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.Popen(
            [exe, "--config", g["config_dir"], "--port", port],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(14):  # ≤7s 等就绪
            time.sleep(0.5)
            if _ping_url(g["url"]):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


class LLMGateway:
    """素月 → Go LLM 网关的轻量 HTTP 客户端（保持原 chat() 返回格式不变）。"""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 60,
    ):
        # base_url/api_key 仅供诊断透传（网关统一管理渠道与 key）
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        g = _resolve_gateway_config()
        self._gateway_url = g["url"]
        self._gateway_exe = g["exe"]
        self._gateway_config = g["config_dir"]
        self._port = int(self._gateway_url.rsplit(":", 1)[-1])
        self._ready = True
        self._init_error = None

    @property
    def ready(self) -> bool:
        return self._ready

    # ---- 网关探活 / 自动拉起 ----

    def _ping(self, timeout: float = 1.5) -> bool:
        return _ping_url(self._gateway_url, timeout)

    def _ensure_gateway_up(self) -> bool:
        """网关不可达时尝试自动拉起；成功返回 True。"""
        if self._ping():
            return True
        if ensure_gateway_up():
            return self._ping()
        return False

    # ---- 对话 ----

    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None,
             tool_choice: str = "auto", temperature: Optional[float] = None) -> Dict:
        """调用 Go 网关 /v1/chat。

        返回（与原实现键一致，追加网关信息）:
          content / reasoning_content / tool_calls / finish_reason
          model / base_url / failover_note / failover_applied
          error / error_code（网关容灾失败时）
        """
        payload: Dict[str, Any] = {
            "messages": messages,
            "temperature": self.temperature if temperature is None else float(temperature),
            "max_tokens": self.max_tokens,
        }
        model_name = str(getattr(self, "model", "") or "").lower()
        if tools:
            # DeepSeek 思考模型（flash/reasoner）只接受 tool_choice="auto"：
            # "required" 与"指定具体函数"均返回 HTTP 400
            # 「Thinking mode does not support this tool_choice」。
            # 2026-09-24 四组对照实测（A带thinking+required / B不带thinking+required /
            # C-auto / D-指定函数），仅 auto 可用。此处统一降级，避免 400 沿调用链
            # 变成"LLM 调用失败，任务中断"。
            if tool_choice not in (None, "auto", "none") and model_name.startswith("deepseek"):
                tool_choice = "auto"
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        # DeepSeek 思考预算：限制每轮 reasoning 输出，显著降延迟（其他模型不支持该参数，跳过）
        # 2026-09-15 诊断：素月每轮 2-8s（偶尔 20s+）慢的根因是 thinking 全量思考；
        # budget_tokens 设上限后简单轮次 1-2s 可回，复杂推理（计划线）仍够用。
        if model_name.startswith("deepseek"):
            payload["thinking"] = {"type": "enabled", "budget_tokens": _DEEPSEEK_THINKING_BUDGET}
        data = json.dumps(payload).encode("utf-8")
        last_err: Optional[Exception] = None
        resp_data: Optional[Dict] = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    self._gateway_url + "/v1/chat", data=data,
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    resp_data = json.loads(r.read().decode("utf-8"))
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt == 0:
                    # 网关不可达（连接失败/超时）→ 尝试拉起一次再重试
                    if self._ensure_gateway_up():
                        continue
                break
        if resp_data is None:
            return {
                "content": None, "tool_calls": [], "finish_reason": "error",
                "error": f"LLM 网关不可达（{self._gateway_url}）: {last_err}。"
                         "已尝试自动拉起，仍失败——请确认 bailing-gateway.exe 可执行",
                "error_code": "network",
            }
        if resp_data.get("error"):
            return {
                "content": None, "tool_calls": [], "finish_reason": "error",
                "error": resp_data.get("error", "LLM 调用失败"),
                "error_code": resp_data.get("error_code", "error"),
                "model": resp_data.get("model"),
                "base_url": resp_data.get("base_url"),
                "failover_note": resp_data.get("failover_note"),
            }
        # —— 推理预算保护（2026-09-23）——
        # 推理模型（deepseek-flash 等）的 reasoning 与 content 共用 max_tokens 预算：
        # 思考链吃光额度 → content 为空串 → 主循环误判"空回复"直接结束（"话说到一半空白"）。
        # 机械可修复场景（无 error + content 空 + reasoning 非空 + finish_reason==length 截断）
        # → 放大 max_tokens 重试一次，不把问题留给用户"重发一次"。
        if (not (resp_data.get("content") or "").strip()
                and resp_data.get("reasoning_content")
                and resp_data.get("finish_reason") == "length"):
            boost = min(16384, max(4096, int(self.max_tokens or 4096) * 2))
            if boost != int(self.max_tokens or 4096):
                payload["max_tokens"] = boost
                data = json.dumps(payload).encode("utf-8")
                try:
                    req = urllib.request.Request(
                        self._gateway_url + "/v1/chat", data=data,
                        headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(req, timeout=self.timeout) as r:
                        resp_data = json.loads(r.read().decode("utf-8"))
                except Exception:  # noqa: BLE001 —— 重试失败就用原结果，交给上层兜底
                    pass
        # 正常回复：透传网关信息（容灾已由网关完成，素月只消费结果）
        tcs = resp_data.get("tool_calls") or []
        return {
            "content": resp_data.get("content"),
            "reasoning_content": resp_data.get("reasoning_content"),
            "tool_calls": [
                {"id": t.get("id", ""), "name": t.get("name", ""),
                 "arguments": t.get("arguments", "{}")}
                for t in tcs
            ],
            "finish_reason": resp_data.get("finish_reason"),
            "model": resp_data.get("model"),
            "base_url": resp_data.get("base_url"),
            "failover_note": resp_data.get("failover_note"),
            "failover_applied": bool(resp_data.get("failover_applied")),
            # 2026-09-19 升级（#287）：透传 API Usage（prompt_tokens/completion_tokens），
            # 作为 token 估算的权威来源（网关未返回时为 None，由本地估算器兜底）
            "usage": resp_data.get("usage"),
        }

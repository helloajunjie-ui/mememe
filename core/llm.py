"""LLM Gateway：封装任意 OpenAI 兼容端点的通信。

设计意图（见设计文档 4.5）：
- 唯一职责：屏蔽上游差异。base_url/model 可配置，可切 DeepSeek / Grok / Gemini / 本地 Ollama。
- 支持 function calling（工具调用）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


class LLMGateway:
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 60,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client = None
        self._ready = False
        self._init_client()

    def _init_client(self) -> None:
        if not self.api_key:
            self._ready = False
            return
        try:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
            self._ready = True
        except Exception as e:  # noqa: BLE001
            self._ready = False
            self._init_error = str(e)

    @property
    def ready(self) -> bool:
        return self._ready

    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None,
             tool_choice: str = "auto") -> Dict:
        """调用 chat/completions。

        tool_choice: "auto"（模型自主）/ "required"（强制调用一个工具）/ "none"。
        返回: {"content": str|None, "tool_calls": [{"id","name","arguments"}], "finish_reason": str}
        """
        if not self._ready:
            return {
                "content": None,
                "tool_calls": [],
                "finish_reason": "error",
                "error": (
                    "LLM 未就绪：缺少 API key。"
                    f"请设置环境变量 BAILING_API_KEY（base_url={self.base_url}, model={self.model}）"
                ),
            }
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        try:
            resp = self._client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            tool_calls = []
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    })
            return {
                "content": msg.content,
                "reasoning_content": getattr(msg, "reasoning_content", None),
                "tool_calls": tool_calls,
                "finish_reason": resp.choices[0].finish_reason,
            }
        except Exception as e:  # noqa: BLE001
            return {
                "content": None,
                "tool_calls": [],
                "finish_reason": "error",
                "error": f"LLM 调用失败: {type(e).__name__}: {e}",
            }

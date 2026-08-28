"""OpenAI 兼容适配器（DeepSeek / OpenAI / Kimi / 通义千问 / GLM 等）。

手写 SSE 流式解析 + tool_calls 按 index 增量拼接。
兼容任何遵循 OpenAI 聊天完成接口格式的 API。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..core.events import TypedEventBus
from ..core.types import Message, ToolCall, ToolDefinition
from .base import BaseLLMAdapter, LLMError

logger = logging.getLogger("litecode.llm")


class OpenAICompatAdapter(BaseLLMAdapter):
    name = "openai-compat"
    provider_id = ""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout: float = 120.0,
        temperature: float = 0.2,
        provider_id: str = "deepseek",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.provider_id = provider_id
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _build_payload(
        self, messages: List[Message], tools: List[ToolDefinition]
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": t.__dict__} for t in tools
            ]
        return payload

    async def chat_stream(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        events: Optional[TypedEventBus] = None,
    ) -> Tuple[str, List[ToolCall]]:
        client = self._get_client()
        payload = self._build_payload(messages, tools)

        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise LLMError(
                        f"[LLM Error] HTTP {response.status_code}: {body}"
                    )
                return await self._parse_sse(response, events)
        except httpx.TimeoutException as exc:
            raise LLMError(f"[LLM Error] 请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"[LLM Error] 网络错误: {exc}") from exc

    async def _parse_sse(
        self, response: httpx.Response, events: Optional[TypedEventBus]
    ) -> Tuple[str, List[ToolCall]]:
        full_content = ""
        tool_calls_map: Dict[int, ToolCall] = {}
        buffer = ""

        async for chunk in response.aiter_bytes():
            buffer += chunk.decode("utf-8", errors="replace")
            lines = buffer.split("\n")
            buffer = lines.pop()

            for line in lines:
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line == "data: [DONE]":
                    return full_content, self._finalize_tool_calls(tool_calls_map)
                if not line.startswith("data: "):
                    continue

                try:
                    parsed = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                delta = (parsed.get("choices") or [{}])[0].get("delta")
                if not delta:
                    continue

                if delta.get("content"):
                    full_content += delta["content"]
                    if events:
                        await events.emit("llm:stream", {"chunk": delta["content"]})

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    target = tool_calls_map.get(idx)
                    if target is None:
                        target = ToolCall(id=tc.get("id", ""), name="", arguments="")
                        tool_calls_map[idx] = target
                    if tc.get("id"):
                        target.id = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        target.name += fn["name"]
                    if fn.get("arguments"):
                        target.arguments += fn["arguments"]

        return full_content, self._finalize_tool_calls(tool_calls_map)

    @staticmethod
    def _finalize_tool_calls(tool_calls_map: Dict[int, ToolCall]) -> List[ToolCall]:
        calls = [tool_calls_map[i] for i in sorted(tool_calls_map)]
        return [c for c in calls if c.name]

    async def test_connection(self) -> Tuple[bool, str, float]:
        start = time.time()
        try:
            client = self._get_client()
            async with client.stream(
                "GET",
                f"{self.base_url}/models",
                headers=self._headers(),
            ) as resp:
                elapsed = (time.time() - start) * 1000
                if resp.status_code == 200:
                    return True, f"连接成功 ({int(elapsed)}ms)", elapsed
                body = (await resp.aread()).decode("utf-8", errors="replace")[:200]
                return False, f"HTTP {resp.status_code}: {body}", elapsed
        except Exception as exc:
            elapsed = (time.time() - start) * 1000
            return False, str(exc)[:150], elapsed
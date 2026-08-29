"""Anthropic Claude 适配器。

与 OpenAI 兼容接口不同：使用 x-api-key 头、messages API、不同的 SSE 事件结构。
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
from .base import BaseLLMAdapter, LLMError, decode_utf8_incremental

logger = logging.getLogger("litecode.llm")


class AnthropicAdapter(BaseLLMAdapter):
    name = "anthropic"
    provider_id = "anthropic"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1",
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 120.0,
        temperature: float = 0.2,
        max_tokens: int = 8192,
        provider_id: str = "anthropic",
        enable_cache: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.provider_id = provider_id
        self.enable_cache = enable_cache
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
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    @staticmethod
    def _to_anthropic_messages(messages: List[Message]) -> List[Dict[str, Any]]:
        """把统一 Message 结构转成 Anthropic 的 user/assistant 消息格式。"""
        out: List[Dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                # Anthropic 用 user 消息携带 tool_result
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content or "",
                    }],
                })
            elif m.role == "assistant":
                content: List[Dict[str, Any]] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for tc in m.tool_calls or []:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": json.loads(tc.arguments) if tc.arguments else {},
                    })
                out.append({"role": "assistant", "content": content})
            else:  # user
                out.append({"role": "user", "content": m.content or ""})
        return out

    def _build_payload(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        system: Optional[str],
        enable_cache: bool = True,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
            "messages": self._to_anthropic_messages(messages),
        }
        if system:
            if enable_cache:
                payload["system"] = [
                    {"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}
                ]
            else:
                payload["system"] = [{"type": "text", "text": system}]
        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]
            if enable_cache and payload["tools"]:
                payload["tools"][-1]["cache_control"] = {"type": "ephemeral"}
        return payload

    async def chat_stream(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        events: Optional[TypedEventBus] = None,
    ) -> Tuple[str, List[ToolCall]]:
        client = self._get_client()

        # 提取 system 消息（Anthropic 单独字段）
        system = None
        if messages and messages[0].role == "system":
            system = messages[0].content

        payload = self._build_payload(messages, tools, system, enable_cache=self.enable_cache)

        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=payload,
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise LLMError(f"[Anthropic Error] HTTP {response.status_code}: {body}")
                return await self._parse_sse(response, events)
        except httpx.TimeoutException as exc:
            raise LLMError(f"[Anthropic Error] 请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"[Anthropic Error] 网络错误: {exc}") from exc

    async def _parse_sse(
        self, response: httpx.Response, events: Optional[TypedEventBus]
    ) -> Tuple[str, List[ToolCall]]:
        full_content = ""
        tool_calls: List[ToolCall] = []
        current_tool: Optional[ToolCall] = None
        buffer = ""
        byte_buffer = b""

        async for chunk in response.aiter_bytes():
            text, byte_buffer = decode_utf8_incremental(byte_buffer, chunk)
            buffer += text
            lines = buffer.split("\n")
            buffer = lines.pop()

            for line in lines:
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line == "event: message_stop":
                    break
                if not line.startswith("data: "):
                    continue

                try:
                    parsed = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                ev_type = parsed.get("type", "")
                if ev_type == "content_block_start":
                    block = parsed.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool = ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments="",
                        )
                        tool_calls.append(current_tool)
                elif ev_type == "content_block_delta":
                    delta = parsed.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        full_content += text
                        if events:
                            await events.emit("llm:stream", {"chunk": text})
                    elif delta.get("type") == "input_json_delta" and current_tool:
                        current_tool.arguments += delta.get("partial_json", "")
                elif ev_type == "content_block_stop":
                    current_tool = None

        return full_content, [tc for tc in tool_calls if tc.name]

    async def test_connection(self) -> Tuple[bool, str, float]:
        start = time.time()
        try:
            client = self._get_client()
            payload = {
                "model": self.model,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "hi"}],
            }
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=payload,
            ) as resp:
                elapsed = (time.time() - start) * 1000
                if resp.status_code in (200, 201):
                    return True, f"连接成功 ({int(elapsed)}ms)", elapsed
                body = (await resp.aread()).decode("utf-8", errors="replace")[:200]
                return False, f"HTTP {resp.status_code}: {body}", elapsed
        except Exception as exc:
            elapsed = (time.time() - start) * 1000
            return False, str(exc)[:150], elapsed
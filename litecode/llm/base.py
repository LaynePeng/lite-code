"""LLM 适配器抽象基类。"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..core.events import TypedEventBus
from ..core.types import Message, ToolCall, ToolDefinition


class LLMError(Exception):
    pass


class BaseLLMAdapter:
    """LLM 适配器抽象接口。

    chat_stream(messages, tools) -> (content, tool_calls)
    流式输出通过事件总线以 "llm:stream" 事件实时广播。
    """

    name = "base"
    provider_id = ""

    async def chat_stream(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        events: Optional[TypedEventBus] = None,
    ) -> Tuple[str, List[ToolCall]]:
        raise NotImplementedError

    async def test_connection(self) -> Tuple[bool, str, float]:
        """测试连接，返回 (是否成功, 消息, 延迟ms)。"""
        raise NotImplementedError
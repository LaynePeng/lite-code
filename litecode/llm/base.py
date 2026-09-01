"""LLM 适配器抽象基类。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core.events import TypedEventBus
from ..core.types import Message, ToolCall, ToolDefinition


def decode_utf8_incremental(buffer: bytes, chunk: bytes) -> Tuple[str, bytes]:
    data = buffer + chunk
    try:
        return data.decode("utf-8"), b""
    except UnicodeDecodeError as exc:
        # 只缓存确实未完成的字符。固定保留末尾 3 字节会在一个 chunk
        # 同时包含合法文本和半个汉字时，把前面的完整字符错误地替换掉。
        if exc.reason == "unexpected end of data" and exc.start < len(data):
            return data[:exc.start].decode("utf-8"), data[exc.start:]
        return data.decode("utf-8", errors="replace"), b""


def clean_custom_headers(raw: Optional[Any]) -> Dict[str, str]:
    """清洗用户自定义 HTTP 头：仅保留 str:str、去空键空值。

    作为适配器构造的最终防线——配置层校验失守时保证非法值不进真实请求。
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: Dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        key = k.strip()
        value = v.strip()
        if key and value:
            cleaned[key] = value
    return cleaned


class LLMError(Exception):
    pass


class BaseLLMAdapter:
    """LLM 适配器抽象接口。

    chat_stream(messages, tools) -> (content, tool_calls, usage)
    流式输出通过事件总线以 "llm:stream" 事件实时广播。
    usage 为模型返回的 token 统计（准确值），无返回时可为 None：
      {prompt_tokens, completion_tokens, prompt_cache_hit_tokens}
    """

    name = "base"
    provider_id = ""

    async def chat_stream(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        events: Optional[TypedEventBus] = None,
    ) -> Tuple[str, List[ToolCall], Optional[Dict[str, Any]]]:
        raise NotImplementedError

    async def test_connection(self) -> Tuple[bool, str, float]:
        """测试连接，返回 (是否成功, 消息, 延迟ms)。"""
        raise NotImplementedError

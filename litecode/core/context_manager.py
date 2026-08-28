"""上下文滑动窗口裁剪（对应课程第3课 ContextManager）。

关键约束（绝对不能违反）：
1. Index 0 的 System Prompt 永远不能删；
2. assistant(tool_calls) 与其后的 tool(result) 必须作为原子对存在或一起被裁剪，
   否则 LLM API 会直接报 HTTP 400。
"""
from __future__ import annotations

import logging
from typing import List

from .token_counter import TokenCounter
from .types import Message

logger = logging.getLogger("litecode.context")


class ContextManager:
    def __init__(self, max_allowed_tokens: int = 48000) -> None:
        self.max_allowed_tokens = max_allowed_tokens

    def prune_messages(self, messages: List[Message]) -> List[Message]:
        current = TokenCounter.count_messages_tokens(messages)
        if current <= self.max_allowed_tokens:
            return messages

        logger.warning(
            "[ContextManager] Exceeded token budget (%s/%s). Pruning...",
            current,
            self.max_allowed_tokens,
        )

        system_message = messages[0] if messages and messages[0].role == "system" else None
        removable = messages[1:] if system_message else list(messages)

        while TokenCounter.count_messages_tokens(
            ([system_message] if system_message else []) + removable
        ) > self.max_allowed_tokens and len(removable) > 2:
            first = removable[0]

            if first.role == "assistant" and first.tool_calls:
                tool_call_ids = {c.id for c in first.tool_calls}
                removable.pop(0)
                while (
                    removable
                    and removable[0].role == "tool"
                    and removable[0].tool_call_id in tool_call_ids
                ):
                    removable.pop(0)
            else:
                removable.pop(0)

        result = ([system_message] if system_message else []) + removable
        logger.info("[ContextManager] Pruned to %s messages.", len(result))
        return result
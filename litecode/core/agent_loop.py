"""AgentLoop 主循环状态机（对应课程第14课 + 第2/3课全部增强）。

完整 Think-Act-Observe 闭环：
LLM 调用(带动态 System Prompt / Token 预算裁剪 / beforeLLM 管道)
  → 解析 tool_calls（JSON 自愈 / 死循环检测 / beforeTool 安全管道 / 审批）
  → 执行工具（超时 / 输出截断 / afterTool 管道）
  → 结果回填消息链 → 会话落盘 → 回到 LLM 调用
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .context_manager import ContextManager
from .json_repair import safe_json_parse
from .kernel import Kernel
from .session_store import SessionStore
from .state_tracker import AgentStateTracker, AgentStatus
from .system_prompt import SystemPromptBuilder
from .token_counter import TokenCounter
from .truncator import truncate_tool_output
from .types import Message, ToolCall, ToolDefinition

logger = logging.getLogger("litecode.agentloop")


class AgentLoop:
    def __init__(
        self,
        kernel: Kernel,
        adapter,
        registry,
        session_store: Optional[SessionStore] = None,
        context_manager: Optional[ContextManager] = None,
        max_steps: int = 25,
        tool_timeout: float = 120.0,
        token_budget: int = 48000,
        pricing: Optional[Dict[str, float]] = None,
        auto_approve: bool = False,
        context_window: Optional[int] = None,
    ) -> None:
        self.kernel = kernel
        self.adapter = adapter
        self.registry = registry
        self.session_store = session_store
        self.context_manager = context_manager or ContextManager(token_budget)
        self.max_steps = max_steps
        self.tool_timeout = tool_timeout
        self.auto_approve = auto_approve
        self.pricing = pricing or {"input_per_mtok": 2.0, "output_per_mtok": 8.0}
        self.context_window = context_window or 128_000
        self.state = AgentStateTracker()
        self.abort_event: Optional[asyncio.Event] = None
        self.workspace: str = "."
        # 截断落盘目录（第4/5课：超限工具输出保存到磁盘，上下文只放句柄）
        self.truncation_dir: Optional[str] = None
        # 上下文压缩/缓存统计（供「上下文情况」面板）
        self._compression_count = 0
        self._compressed_tokens = 0
        self._last_usage: Optional[Dict[str, int]] = None

    def request_stop(self) -> None:
        if self.abort_event:
            self.abort_event.set()
        self.state.status = AgentStatus.STOPPED

    def _check_abort(self) -> bool:
        return bool(self.abort_event and self.abort_event.is_set())

    # ------------------------------------------------------------------ 主循环

    async def run_task(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[ToolDefinition]] = None,
        store_snapshot: bool = True,
    ) -> Tuple[str, Dict[str, Any]]:
        messages: List[Message] = self.kernel.ctx.messages
        tools = tools if tools is not None else self.registry.get_tools()
        self.state = AgentStateTracker()
        self.state.status = AgentStatus.RUNNING

        stats: Dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
            "turns": 0,
            "blocked": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
        }

        # 1. System Prompt 初始化（每任务一次：静态骨架，保证缓存前缀稳定）
        if system_prompt is None:
            system_prompt = SystemPromptBuilder.build(self.workspace, tools)
        if not messages or messages[0].role != "system":
            messages.insert(0, Message(role="system", content=system_prompt))
        else:
            messages[0].content = system_prompt

        # 2. 用户消息入链
        user_message = Message(role="user", content=prompt)
        messages.append(user_message)
        await self.kernel.events.emit("message:added", {"message": user_message.to_dict()})

        await self.kernel.events.emit("task:start", {"session_id": self.kernel.session_id})

        current_step = 0
        try:
            while current_step < self.max_steps:
                current_step += 1
                stats["turns"] = current_step
                await self.kernel.events.emit("llm:turn_start", {"turn": current_step})

                if self._check_abort():
                    return await self._finish("[Stopped]: 已由用户手动停止。", messages, stats, store_snapshot)

                # A. 上下文裁剪（保护 system 与 assistant/tool 原子对）
                #    有效上限 = min(预算, 90% × 模型上下文窗口)，到阈值自动压缩
                cap = self._effective_cap()
                payload = self.context_manager.prune_messages(messages, hard_cap=cap)
                if self.context_manager.last_prune.get("compressed"):
                    self._compression_count += 1
                    self._compressed_tokens += int(
                        self.context_manager.last_prune.get("removed_tokens", 0)
                    )

                # B. beforeLLM 管道（插件可修改消息）
                processed = await self.kernel.before_llm.run(self.kernel.ctx, payload)
                if not self._last_usage:
                    stats["input_tokens"] += TokenCounter.count_messages_tokens(processed)

                # D. 调用 LLM（流式，内部 emit llm:stream）
                try:
                    content, tool_calls, usage = await self.adapter.chat_stream(
                        processed, tools, self.kernel.events
                    )
                except Exception as exc:
                    logger.exception("[AgentLoop] LLM 调用失败")
                    messages.append(Message(role="assistant", content=f"[LLM Error]: {exc}"))
                    return await self._finish(f"[LLM Error]: {exc}", messages, stats, store_snapshot)

                # D2. 用模型返回的 usage 累加（准确值），无 usage 时回退估算
                self._last_usage = usage or self._last_usage
                if usage:
                    stats["input_tokens"] += usage.get("prompt_tokens", 0)
                    stats["output_tokens"] += usage.get("completion_tokens", 0)
                    hit = usage.get("prompt_cache_hit_tokens", 0)
                    prompt = usage.get("prompt_tokens", 0)
                    stats["cache_hit_tokens"] += hit
                    if getattr(self.adapter, "name", "") == "anthropic":
                        # Anthropic: input_tokens 不含 cache_read，miss = input_tokens
                        stats["cache_miss_tokens"] += prompt
                    else:
                        # OpenAI 兼容（DeepSeek 等）: prompt_tokens 已含命中部分
                        stats["cache_miss_tokens"] += max(0, prompt - hit)
                else:
                    stats["output_tokens"] += TokenCounter.count_text_tokens(content or "")

                await self._emit_context_stats(stats)

                # E. Assistant 消息入链
                assistant_message = Message(
                    role="assistant",
                    content=content or None,
                    tool_calls=tool_calls if tool_calls else None,
                )
                messages.append(assistant_message)
                await self.kernel.events.emit("message:added", {"message": assistant_message.to_dict()})

                # F. 无工具调用 → 任务收敛，输出最终文本
                if not tool_calls:
                    self.state.status = AgentStatus.SUCCESS
                    return await self._finish(content or "(空回复)", messages, stats, store_snapshot)

                # G. 顺序派发执行工具
                for call in tool_calls:
                    if self._check_abort():
                        return await self._finish("[Stopped]: 已由用户手动停止。", messages, stats, store_snapshot)

                    result_text = await self._execute_tool_call(call, stats)
                    tool_result = Message(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content=result_text,
                    )
                    messages.append(tool_result)
                    await self.kernel.events.emit("message:added", {"message": tool_result.to_dict()})

                # H. 每轮批量执行完成后落盘，防中途崩溃丢状态
                if store_snapshot:
                    self._save_session()

                await self.kernel.events.emit("stats:update", self._stats_payload(stats))

            return await self._finish(
                "[Loop Terminated]: 超出最大步骤限制仍未得出最终结论。",
                messages, stats, store_snapshot,
            )
        except asyncio.CancelledError:
            self.state.status = AgentStatus.STOPPED
            self._save_session()
            raise
        except Exception:
            logger.exception("[AgentLoop] 未捕获异常")
            self._save_session()
            raise

    # ------------------------------------------------------------------ 工具执行

    async def _execute_tool_call(self, call: ToolCall, stats: Dict[str, Any]) -> str:
        tool_name = call.name

        # 1. 死循环检测（连续 N 次相同工具+相同参数）
        if self.state.register_and_check_loop(tool_name, call.arguments):
            return (
                f"[Harness Defense]: 检测到死循环！你已用完全相同参数连续调用 {tool_name} "
                f"{self.state.loop_threshold} 次。请停止并换一种策略。"
            )

        # 2. JSON 容错解析（失败回填给 LLM 自愈）
        ok, args, error = safe_json_parse(call.arguments)
        if not ok:
            return f"[Harness Defense]: {error}"

        # 3. beforeTool 安全管道（SecurityPlugin 等）
        hook_data = {"toolName": tool_name, "args": args, "cancel": False, "reason": ""}
        verified = await self.kernel.before_tool.run(self.kernel.ctx, hook_data)

        start_time = time.time()
        if verified.get("cancel"):
            stats["blocked"] += 1
            result_text = f"[Tool Execution Cancelled]: {verified.get('reason') or '被安全策略拒绝。'}"
        else:
            await self.kernel.events.emit(
                "tool:before_execute", {"toolName": tool_name, "args": args}
            )
            try:
                raw = await asyncio.wait_for(
                    self.registry.execute(tool_name, args), timeout=self.tool_timeout
                )
            except asyncio.TimeoutError:
                raw = f"[Tool Timeout]: 工具 {tool_name} 执行超过 {self.tool_timeout}s 被终止。"
            except Exception as exc:  # 注册表内已捕获，这里兜底
                raw = f"[Execution Exception]: {exc}"
            result_text = truncate_tool_output(raw, output_dir=self.truncation_dir).content
            stats["tool_calls"] += 1

        duration_ms = int((time.time() - start_time) * 1000)

        # 4. afterTool 管道（结果修饰）
        try:
            post = await self.kernel.after_tool.run(
                self.kernel.ctx,
                {"toolName": tool_name, "result": result_text, "args": args},
            )
            result_text = post.get("result", result_text)
        except Exception:
            pass

        await self.kernel.events.emit(
            "tool:after_execute",
            {"toolName": tool_name, "durationMs": duration_ms,
             "status": "cancelled" if verified.get("cancel") else "success"},
        )
        return result_text

    # ------------------------------------------------------------------ 收尾

    async def _finish(self, content: str, messages: List[Message], stats: Dict[str, Any],
                      store_snapshot: bool) -> Tuple[str, Dict[str, Any]]:
        if store_snapshot:
            self._save_session()
        payload = self._stats_payload(stats)
        await self.kernel.events.emit("task:done", {"content": content, "stats": payload})
        return content, payload

    def _save_session(self) -> None:
        if self.session_store is not None:
            try:
                self.session_store.save(
                    self.kernel.session_id, self.kernel.ctx.messages,
                    {"updated_by": "agent_loop"},
                )
            except Exception:
                logger.exception("[AgentLoop] 会话落盘失败")

    def _stats_payload(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        input_t = stats["input_tokens"]
        output_t = stats["output_tokens"]
        cost = (
            input_t / 1_000_000 * self.pricing.get("input_per_mtok", 0)
            + output_t / 1_000_000 * self.pricing.get("output_per_mtok", 0)
        )
        return {
            **stats,
            "cost_estimate": round(cost, 4),
            "status": self.state.status.value,
        }

    def _effective_cap(self) -> int:
        """上下文有效上限 = min(预算, 90% × 模型上下文窗口)。"""
        window_cap = int(0.9 * self.context_window)
        return min(self.context_manager.max_allowed_tokens, window_cap)

    async def _emit_context_stats(self, stats: Dict[str, Any]) -> None:
        """推送「上下文情况」统计（任务内实时，会话累计由 TaskHandle 合并）。"""
        hit = stats.get("cache_hit_tokens", 0)
        miss = stats.get("cache_miss_tokens", 0)
        hit_rate = round(hit / (hit + miss), 4) if (hit + miss) > 0 else None
        last_usage = self._last_usage or {}
        prompt_tokens = last_usage.get("prompt_tokens") or stats.get("input_tokens", 0)
        usage_ratio = round(prompt_tokens / self.context_window, 4) if self.context_window else None
        model = getattr(self.adapter, "model", None) or ""
        await self.kernel.events.emit("context:stats", {
            "model": model,
            "context_window": self.context_window,
            "task": {
                "prompt_tokens": stats.get("input_tokens", 0),
                "output_tokens": stats.get("output_tokens", 0),
                "cache_hit_tokens": hit,
                "cache_miss_tokens": miss,
                "cache_hit_rate": hit_rate,
                "compression_count": self._compression_count,
                "compressed_tokens": self._compressed_tokens,
                "usage_ratio": usage_ratio,
                "last_prompt_tokens": prompt_tokens,
            },
        })
"""任务运行器：管理并发任务、SSE 事件队列、审批挂起。"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from ..app import AgentApp
from ..core.agent_loop import AgentLoop
from ..core.system_prompt import SystemPromptBuilder
from ..core.types import Message

logger = logging.getLogger("litecode.tasks")

EVENT_FORWARD = {
    "llm:stream", "llm:turn_start", "message:added", "tool:before_execute",
    "tool:after_execute", "approval:request", "approval:resolved", "task:start",
    "task:done", "task:error", "stats:update", "subagent:completed",
    "context:stats", "subagent:started",
}


class TaskHandle:
    def __init__(self, task_id: str, kernel, registry: Any, loop: AgentLoop, app: AgentApp) -> None:
        self.task_id = task_id
        self.kernel = kernel
        self.registry = registry
        self.loop = loop
        self.app = app
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self.abort_event = asyncio.Event()
        self.loop.abort_event = self.abort_event
        self.task: Optional[asyncio.Task] = None
        self.stopping = False
        self.running = False
        self.done = False
        self.subscription = None

    def _forward_event(self, data: Any) -> None:
        event_type = data.get("type") if isinstance(data, dict) else ""
        terminal = event_type in {"task:done", "task:error"}
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            # 高频中间事件可以丢弃旧事件，但终止事件必须进入队列。
            try:
                if terminal:
                    while True:
                        self.queue.get_nowait()
                        try:
                            self.queue.put_nowait(data)
                            break
                        except asyncio.QueueFull:
                            continue
                else:
                    self.queue.get_nowait()
                    self.queue.put_nowait(data)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.warning("[Task %s] SSE 队列溢出，丢弃事件", self.task_id)

    def _subscribe_events(self) -> None:
        async def _listener(event_name: str, payload: Any) -> None:
            if event_name == "context:stats":
                # 合并会话级累计（任务内实时 + 会话累计一并推给前端）
                session_stats = self.app.accumulate_context_stats(
                    self.kernel.session_id, (payload or {}).get("task") or {}
                )
                payload = {**(payload or {}), "session": session_stats}
            if event_name in EVENT_FORWARD:
                self._forward_event({"type": event_name, "data": payload})

        self.kernel.events.on("llm:stream", lambda p: _listener("llm:stream", p))
        for name in EVENT_FORWARD - {"llm:stream"}:
            self.kernel.events.on(name, lambda p, n=name: _listener(n, p))

    async def run(self, prompt: str) -> None:
        self._subscribe_events()
        self.running = True
        try:
            system_prompt = SystemPromptBuilder.build(self.app.workspace, self.registry.get_tools())
            await self.loop.run_task(prompt, system_prompt=system_prompt, store_snapshot=True)
        except asyncio.CancelledError:
            self._forward_event({"type": "task:error",
                                 "data": {"message": "[Stopped]: 任务被取消。"}})
        except Exception as exc:
            logger.exception("[Task %s] 运行异常", self.task_id)
            self._forward_event({"type": "task:error", "data": {"message": str(exc)}})
        finally:
            self.running = False
            self.done = True
            # 结束哨兵必须送达，否则客户端会一直处于运行状态。
            while True:
                try:
                    self.queue.put_nowait(None)
                    break
                except asyncio.QueueFull:
                    try:
                        self.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

    def stop(self) -> None:
        """先置协作式中止信号，再强杀挂起的 asyncio 任务（LLM 流卡住时靠它解套）。"""
        self.stopping = True
        self.abort_event.set()
        if self.task is not None and not self.task.done():
            self.task.cancel()


class TaskManager:
    def __init__(self, app: AgentApp) -> None:
        self.app = app
        self.tasks: Dict[str, TaskHandle] = {}

    def start(self, session_id: str, prompt: str, agent_id: Optional[str] = None) -> TaskHandle:
        task_id = uuid.uuid4().hex[:12]
        # 按 Agent 配置裁剪工具集（build 全量 / plan 只读 / 自定义）
        registry = self.app.create_agent_registry(agent_id or "build")
        kernel = self.app.create_kernel(session_id, registry=registry)
        # 多轮对话：加载该 session 已落盘的历史消息到上下文，
        # 避免每轮新建 kernel 时从空上下文开始、落盘覆盖上一轮对话
        snapshot = self.app.session_store.load(session_id)
        if snapshot and snapshot.messages:
            kernel.ctx.messages = list(snapshot.messages)
        model_override = (snapshot.metadata.get("model") if snapshot else None) or None
        if not isinstance(model_override, dict):
            model_override = None
        loop = self.app.create_loop(kernel, registry, agent_id=agent_id,
                                    model_override=model_override)

        handle = TaskHandle(task_id, kernel, registry, loop, self.app)
        handle.agent_id = agent_id or "build"
        self.tasks[task_id] = handle
        handle.task = asyncio.get_event_loop().create_task(handle.run(prompt))
        return handle

    def get(self, task_id: str) -> Optional[TaskHandle]:
        return self.tasks.get(task_id)

    def stop(self, task_id: str) -> bool:
        handle = self.tasks.get(task_id)
        if handle is None:
            return False
        handle.stop()
        return True

    def cleanup(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)

    def active_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.running)

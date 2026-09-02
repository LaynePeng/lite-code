"""任务 TODO 清单工具：超长/多步骤任务的规划与进度追踪。

Agent 通过 `todo_write` 全量维护清单（对齐 Claude Code TodoWrite 模式），
每次调用提交完整列表并触发 `todo:updated` 事件实时推送到前端「TODOs」面板。
TaskManager 在任务启动时把会话事件总线绑定到看板、结束时解绑。
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, List

from ..core.types import ToolDefinition
from .plugin import ToolPlugin

# 当前任务所属会话（agent_loop.run_task 启动时设置；工具处理器运行时读取）
current_session_id: contextvars.ContextVar = contextvars.ContextVar(
    "current_session_id", default=""
)

VALID_STATUSES = ("pending", "in_progress", "completed")
MAX_TODOS = 100

_STATUS_MARKS = {"pending": "☐", "in_progress": "▶", "completed": "✔"}


def _render_board(todos: List[Dict[str, Any]]) -> str:
    return "\n".join(f"{_STATUS_MARKS.get(t['status'], '☐')} {t['content']}" for t in todos)


class TodoPlugin(ToolPlugin):
    """todo_write 工具：任务级 TODO 看板（校验 + 存储 + 事件推送）。"""

    name = "todo-plugin"

    def __init__(self) -> None:
        self._items: Dict[str, List[Dict[str, Any]]] = {}
        self._events: Dict[str, Any] = {}  # session_id -> EventBus

    # ------------------------------------------------------------ 生命周期

    def bind(self, session_id: str, events: Any) -> None:
        """任务启动时绑定会话事件总线（TaskManager 调用）。"""
        self._events[session_id] = events

    def unbind(self, session_id: str) -> None:
        self._events.pop(session_id, None)

    def get(self, session_id: str) -> List[Dict[str, Any]]:
        """读取会话当前清单（最近一次 todo_write 的结果）。"""
        return list(self._items.get(session_id, []))

    # ------------------------------------------------------------ 工具

    def get_tools(self) -> List[ToolDefinition]:
        return [ToolDefinition(
            name="todo_write",
            description=(
                "维护当前任务的 TODO 清单（多步骤/超长任务必用，琐碎单步任务不要用）。"
                "每次调用提交完整清单（全量覆盖，不是增量追加）。使用规则："
                "①接到复杂任务先列计划再动手，每项是一个可验证的小步骤；"
                "②同一时间只允许一项 in_progress；"
                "③开始某项前置为 in_progress，完成后立即置为 completed；"
                "④过程中发现新的工作项及时补进清单；"
                "⑤全部完成后所有项应为 completed。"
                "用户可在右侧 TODOs 面板实时看到清单与进度。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "完整的 TODO 列表（全量覆盖）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "待办事项描述"},
                                "status": {
                                    "type": "string",
                                    "enum": list(VALID_STATUSES),
                                    "description": "pending(待办) / in_progress(进行中) / completed(已完成)",
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        )]

    async def execute(self, name: str, args: Dict[str, Any]) -> str:
        session_id = current_session_id.get("")
        if not session_id:
            return "[Error] 当前没有活动会话，无法维护 TODO"
        todos = args.get("todos")
        if not isinstance(todos, list):
            return "[Error] todos 必须是数组，且每次提交完整清单（全量覆盖）"
        if len(todos) > MAX_TODOS:
            return f"[Error] TODO 数量超过上限 {MAX_TODOS}"
        cleaned: List[Dict[str, Any]] = []
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                return f"[Error] 第 {i + 1} 项必须是对象（content/status）"
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).strip()
            if not content:
                return f"[Error] 第 {i + 1} 项 content 不能为空"
            if status not in VALID_STATUSES:
                return (f"[Error] 第 {i + 1} 项 status 非法: {status!r}"
                        f"（允许: {', '.join(VALID_STATUSES)}）")
            cleaned.append({"content": content, "status": status})

        self._items[session_id] = cleaned
        events = self._events.get(session_id)
        if events is not None:
            try:
                await events.emit("todo:updated", {"todos": list(cleaned)})
            except Exception:
                pass  # 事件推送失败不影响工具结果

        done = sum(1 for t in cleaned if t["status"] == "completed")
        doing = sum(1 for t in cleaned if t["status"] == "in_progress")
        header = f"TODO 清单已更新：共 {len(cleaned)} 项（✔{done} ▶{doing} ☐{len(cleaned) - done - doing}）"
        return f"{header}\n{_render_board(cleaned)}"

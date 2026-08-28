"""安全中间件插件（对应课程第15课 SecurityPlugin，Web 审批版）。

挂载到 Kernel.beforeTool 管道：
- 文件/编辑工具 → 敏感路径检查（HIGH 阻断）
- execute_command → 高危黑名单（HIGH 阻断）/ 中危（Web 审批）/ 白名单放行
- sudo 提权、强制推送、删库等中危操作 → 弹审批卡等待用户确认
"""
from __future__ import annotations

import logging
from typing import Any

from ..core.kernel import Kernel
from ..core.types import Plugin
from .approval import ApprovalGate
from .guard import SecurityCheckResult, SecurityGuard, ThreatLevel

logger = logging.getLogger("litecode.security")


class SecurityPlugin(Plugin):
    name = "security-plugin"

    def __init__(self, guard: SecurityGuard, approval_gate: ApprovalGate) -> None:
        self.guard = guard
        self.approval_gate = approval_gate

    def install(self, kernel: Kernel) -> None:
        @kernel.before_tool.use
        async def _middleware(ctx, data, next):
            tool_name = data.get("toolName", "")
            args = data.get("args", {}) or {}

            # 路径型工具过滤
            path_result = self.guard.check_tool(tool_name, args)
            if path_result.level == ThreatLevel.HIGH:
                data["cancel"] = True
                data["reason"] = f"[SecurityGuard]: {path_result.reason}"
                return await next(data)

            # Shell 指令过滤
            if tool_name == "execute_command":
                command = args.get("command", "")
                result: SecurityCheckResult = self.guard.check_shell_command(command)

                if result.level == ThreatLevel.HIGH:
                    data["cancel"] = True
                    data["reason"] = f"[Blocked by SecurityGuard]: {result.reason}"
                    return await next(data)

                if result.level == ThreatLevel.MEDIUM:
                    approved = await self._request_approval(
                        kernel, f'execute_command("{command}")', result.reason or "中危操作"
                    )
                    if not approved:
                        data["cancel"] = True
                        data["reason"] = "[User Rejected]: 操作被操作员明确拒绝。"
                        return await next(data)

            return await next(data)

        kernel.register_service("security_guard", self.guard)

    async def _request_approval(self, kernel: Kernel, action: str, reason: str) -> bool:
        future = self.approval_gate.request_approval(action, reason)
        approval_id = self.approval_gate.current_id(future)
        # 广播审批请求，Web UI 弹出确认卡片
        await kernel.events.emit("approval:request", {
            "id": approval_id,
            "action": action,
            "reason": reason,
        })
        approved = await future
        # 广播审批结果，UI 关闭确认卡片
        await kernel.events.emit("approval:resolved", {
            "id": approval_id,
            "approved": approved,
        })
        return approved
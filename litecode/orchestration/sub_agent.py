"""子 Agent 编排（对应课程第11/14课 SubAgentRunner 真实化）。

上下文隔离：子 Agent 拥有独立 Kernel 与消息链；工具集按角色裁剪；
结果压缩：最终产出汇总为精简报告 + Token 消耗归集到父级事件。

第14课增强：角色来源扩展为 AgentRegistry —— 用户自定义的 subagent
（mode="subagent"）也能被 spawn_sub_agent 直接派生使用。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from ..core.agent_loop import AgentLoop
from ..core.system_prompt import SystemPromptBuilder
from ..core.types import Message, ToolDefinition

logger = logging.getLogger("litecode.orchestration")

ROLE_PROMPTS = {
    "explorer": "你是一名只读调研员。只能查看代码与搜索，禁止修改文件或执行破坏性命令。"
                "聚焦任务，给出简洁结论与关键文件/行号证据。",
    "tester": "你是一名测试执行员。负责运行测试并分析结果，可执行命令但禁止修改生产代码。",
    "refactor": "你是一名重构工程师。拥有完整工具集，负责完成指定的重构任务并验证。",
    "general": "你是一名专注的专家工人，聚焦你的任务并返回简洁总结。",
}

ROLE_TOOLS: Dict[str, List[str]] = {
    "explorer": ["read_file", "list_dir", "file_tree", "search_code", "get_file_outline",
                 "read_focused_symbol", "git_status", "git_diff", "git_log", "git_branch",
                 "review_code", "webfetch", "webfetch_batch"],
    "tester": ["read_file", "list_dir", "file_tree", "search_code", "get_file_outline",
               "read_focused_symbol", "execute_command", "git_status", "git_diff", "git_log",
               "git_branch"],
    "refactor": None,  # 全部工具
    "general": None,
}


class SubAgentRunner:
    def __init__(self, app) -> None:
        self.app = app

    def _resolve_role(self, role: str):
        """从 AgentRegistry 查找角色（支持用户自定义 subagent），否则回退内置 ROLE_*。"""
        try:
            profile = self.app.agent_registry.get(role)
            if profile.mode in ("subagent", "all"):
                return profile
        except KeyError:
            pass
        return None

    async def run_task(
        self,
        task_description: str,
        role: str = "general",
        system_prompt: Optional[str] = None,
        max_steps: int = 12,
        parent_events=None,
    ) -> Dict[str, Any]:
        if role == "explore":
            role = "explorer"
        profile = self._resolve_role(role)
        if profile is not None:
            allowed = profile.tools
            permissions = profile.permissions
            base_prompt = profile.system_prompt or ROLE_PROMPTS.get("general", "")
        else:
            allowed = ROLE_TOOLS.get(role)
            permissions = None
            base_prompt = system_prompt or ROLE_PROMPTS.get(role, ROLE_PROMPTS["general"])

        registry = self.app.build_registry(
            allowed=allowed,
            exclude=["spawn_sub_agent"],  # 子 Agent 不再嵌套派生，防止失控
            permissions=permissions,
        )
        sub_kernel = self.app.create_kernel(f"sub_{uuid.uuid4().hex[:8]}", registry=registry)
        tools: List[ToolDefinition] = registry.get_tools()

        system = (
            f"{base_prompt}\n\n[你的具体任务]\n{task_description}\n\n"
            f"工作目录: {self.app.workspace}\n"
            f"{SystemPromptBuilder._git_info(self.app.workspace)}"
        )

        loop = AgentLoop(
            kernel=sub_kernel,
            adapter=self.app.adapter,
            registry=registry,
            session_store=None,  # 子 Agent 不落盘
            max_steps=max_steps,
            tool_timeout=float(self.app.config.get("tool_timeout", 120)),
            token_budget=int(self.app.config.get("token_budget", 48000)) // 2,
            auto_approve=bool(self.app.config.get("auto_approve", False)),
        )
        loop.workspace = self.app.workspace
        loop.truncation_dir = self.app.create_loop(sub_kernel, registry).truncation_dir

        logger.info('[SubAgent] 派生子 Agent role=%s task="%s..."',
                    role, task_description[:60])

        if parent_events is not None:
            await parent_events.emit("subagent:started", {
                "task": task_description,
                "role": role,
            })

        summary, stats = await loop.run_task(
            f"请完成以下子任务并输出精炼总结（不要向用户提问，直接执行）：\n{task_description}",
            system_prompt=system,
            tools=tools,
            store_snapshot=False,
        )

        completed_payload = {
            "task": task_description,
            "role": role,
            "tokens_used": stats["input_tokens"] + stats["output_tokens"],
            "turns": stats["turns"],
            "summary": summary,
        }
        await sub_kernel.events.emit("subagent:completed", completed_payload)
        if parent_events is not None:
            await parent_events.emit("subagent:completed", completed_payload)

        logger.info('[SubAgent] 完成 role=%s turns=%s tokens=%s',
                    role, stats["turns"], stats["input_tokens"] + stats["output_tokens"])
        return {
            "summary": summary,
            "total_tokens_used": stats["input_tokens"] + stats["output_tokens"],
            "turns": stats["turns"],
            "completed": stats["status"] == "SUCCESS",
            "role": role,
        }

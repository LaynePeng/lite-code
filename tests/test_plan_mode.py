"""Plan 模式只读回归测试：全链路（TaskManager → Kernel 装配 → AgentLoop）不得泄漏写工具。"""
from __future__ import annotations

import os

from litecode.app import AgentApp
from litecode.core.system_prompt import FINAL_REPORT_REQUIREMENT, SystemPromptBuilder
from litecode.server.tasks import TaskManager
from tests.conftest import MockLLMAdapter, tool_call


class RecAdapter(MockLLMAdapter):
    """记录每次发给 LLM 的工具 schema。"""

    def __init__(self, responses):
        super().__init__(responses)
        self.seen_tools: list = []

    async def chat_stream(self, messages, tools, events=None):
        self.seen_tools.append([t.name for t in tools])
        return await super().chat_stream(messages, tools, events)


WRITE_TOOLS = {"write_file", "apply_search_replace", "apply_unified_diff",
               "execute_command", "git_commit", "spawn_sub_agent"}


def test_all_agent_prompts_require_final_report(tmp_path):
    app = AgentApp(workspace=str(tmp_path), config_dir=str(tmp_path / ".lite-code"))
    tools = app.build_registry().get_tools()
    assert FINAL_REPORT_REQUIREMENT in SystemPromptBuilder.build(str(tmp_path), tools)
    assert FINAL_REPORT_REQUIREMENT in app.get_agent("plan").system_prompt


def test_project_instruction_files_are_included(tmp_path):
    (tmp_path / "AGENTS.md").write_text("先运行单元测试。", encoding="utf-8")
    (tmp_path / "Claude.md").write_text("使用项目既有命名规范。", encoding="utf-8")
    prompt = SystemPromptBuilder.build(str(tmp_path), [])
    assert "### 项目指令 (Project Instructions)" in prompt
    assert "### AGENTS.md" in prompt
    assert "先运行单元测试。" in prompt
    assert "### Claude.md" in prompt
    assert "使用项目既有命名规范。" in prompt


async def test_plan_mode_never_leaks_write_tools(tmp_path):
    """plan 任务：发给 LLM 的 schema 只含只读工具，尝试写文件也不会执行。"""
    app = AgentApp(workspace=str(tmp_path), config_dir=str(tmp_path / ".lite-code"))
    app._mock_adapter = RecAdapter([
        ("", [tool_call("write_file", '{"filePath":"x.txt","content":"hi"}', cid="c1")]),
        ("（plan 完成）", []),
    ])
    tm = TaskManager(app)
    handle = tm.start("plan-session", "帮我规划一下", agent_id="plan")
    await handle.task

    # 1. 工具 schema 无任何写工具
    schema = handle.registry.names()
    assert WRITE_TOOLS.isdisjoint(schema), f"plan 模式泄漏写工具: {sorted(WRITE_TOOLS & set(schema))}"
    # 2. 写文件未发生
    assert not os.path.exists(tmp_path / "x.txt")
    # 3. 注册表执行写工具返回未注册错误（防 LLM 越权调用）
    result = await handle.registry.execute("write_file", {"filePath": "x.txt", "content": "hi"})
    assert "未注册" in result


async def test_build_mode_keeps_full_tools(tmp_path):
    """对照：build 模式仍拥有全部工具，写文件正常执行。"""
    app = AgentApp(workspace=str(tmp_path), config_dir=str(tmp_path / ".lite-code"))
    app._mock_adapter = RecAdapter([
        ("", [tool_call("write_file", '{"filePath":"y.txt","content":"hi"}', cid="c2")]),
        ("（build 完成）", []),
    ])
    tm = TaskManager(app)
    handle = tm.start("build-session", "帮我写文件", agent_id="build")
    await handle.task

    assert "write_file" in handle.registry.names()
    assert (tmp_path / "y.txt").exists()


async def test_create_kernel_without_registry_installs_full_tools(tmp_path):
    """create_kernel 不传 registry 时：默认全量工具内核仍可用。"""
    app = AgentApp(workspace=str(tmp_path), config_dir=str(tmp_path / ".lite-code"))
    kernel = app.create_kernel("bare")
    from litecode.tools.registry import ToolRegistry

    registry = kernel.get_service("tools")
    assert isinstance(registry, ToolRegistry)
    assert registry.has("write_file")
    assert registry.has("webfetch")

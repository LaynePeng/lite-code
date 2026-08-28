"""AgentLoop 主循环测试（第14课 + 第2课增强：自愈/死循环/截断/停止/会话落盘）。"""
import asyncio
import os

import pytest

from litecode.core.agent_loop import AgentLoop
from litecode.core.kernel import Kernel
from litecode.core.session_store import SessionStore
from litecode.tools.registry import ToolRegistry
from tests.conftest import MockLLMAdapter, tool_call

SYSTEM_PROMPT = "你是测试 Agent。"


def _make_loop(tmp_path, adapter, registry=None, **kwargs):
    kernel = Kernel("test-session")
    store = SessionStore(str(tmp_path / "sessions"))
    registry = registry or ToolRegistry()
    loop = AgentLoop(kernel=kernel, adapter=adapter, registry=registry,
                     session_store=store, max_steps=10, **kwargs)
    loop.workspace = str(tmp_path)
    return loop, kernel, store


async def test_basic_think_act_observe(tmp_path):
    """写文件 → 回填 → 终答，验证完整闭环与落盘。"""
    registry = ToolRegistry()

    async def write(args):
        p = os.path.join(str(tmp_path), args["filePath"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(args["content"])
        return "[Success] 已写入"

    registry.register("write_file", "写文件", {"type": "object"}, write)

    adapter = MockLLMAdapter([
        ("", [tool_call("write_file", '{"filePath":"a.txt","content":"hi"}')]),
        ("任务完成。", []),
    ])
    loop, kernel, store = _make_loop(tmp_path, adapter, registry)

    events = []
    kernel.events.on("message:added", lambda d: events.append(d["message"]["role"]))

    result, stats = await loop.run_task("创建 a.txt", system_prompt=SYSTEM_PROMPT)

    assert result == "任务完成。"
    assert os.path.exists(tmp_path / "a.txt")
    assert stats["tool_calls"] == 1
    assert events.count("user") >= 1
    assert events.count("assistant") >= 2
    assert events.count("tool") == 1
    # 会话落盘
    snap = store.load("test-session")
    assert snap is not None and len(snap.messages) >= 5


async def test_loop_detection(tmp_path):
    """连续 3 次相同工具+参数 → 触发死循环防御并注入错误消息。"""
    registry = ToolRegistry()
    registry.register("read_file", "读文件", {"type": "object"},
                      lambda args: "content")

    adapter = MockLLMAdapter([
        ("", [tool_call("read_file", '{"filePath":"x.ts"}')]),
        ("", [tool_call("read_file", '{"filePath":"x.ts"}')]),
        ("", [tool_call("read_file", '{"filePath":"x.ts"}')]),
        ("好吧，我换策略。", []),
    ])
    loop, kernel, _ = _make_loop(tmp_path, adapter, registry)

    result, stats = await loop.run_task("读文件", system_prompt=SYSTEM_PROMPT)
    tool_msgs = [m for m in kernel.ctx.messages if m.role == "tool"]
    assert any("死循环" in m.content for m in tool_msgs)
    assert result == "好吧，我换策略。"


async def test_json_self_heal(tmp_path):
    """非法 JSON 参数 → 回填错误让 LLM 自愈，不 crash。"""
    registry = ToolRegistry()
    registry.register("read_file", "读文件", {"type": "object"}, lambda args: "ok")

    adapter = MockLLMAdapter([
        ("", [tool_call("read_file", "{not valid json")]),
        ("修正了。", []),
    ])
    loop, kernel, _ = _make_loop(tmp_path, adapter, registry)

    result, _ = await loop.run_task("读文件", system_prompt=SYSTEM_PROMPT)
    tool_msgs = [m for m in kernel.ctx.messages if m.role == "tool"]
    assert any("JSON Parse Failed" in m.content for m in tool_msgs)
    assert result == "修正了。"


async def test_output_truncation(tmp_path):
    """工具输出超长 → 截断后进入消息链。"""
    registry = ToolRegistry()
    registry.register("big_tool", "大输出", {"type": "object"},
                      lambda args: "A" * 50000)

    adapter = MockLLMAdapter([
        ("", [tool_call("big_tool", "{}")]),
        ("完成", []),
    ])
    loop, kernel, _ = _make_loop(tmp_path, adapter, registry)

    await loop.run_task("跑大输出", system_prompt=SYSTEM_PROMPT)
    tool_msgs = [m for m in kernel.ctx.messages if m.role == "tool"]
    assert tool_msgs and len(tool_msgs[0].content) < 50000
    assert "截断" in tool_msgs[0].content


async def test_abort_stop(tmp_path):
    """停止信号在工具批次边界生效（协作式中断）。"""
    registry = ToolRegistry()
    loop_ref = {}

    async def trigger(args):
        loop_ref["loop"].abort_event.set()  # 第一个工具触发停止
        return "ok"

    registry.register("trigger_stop", "触发停止", {"type": "object"}, trigger)
    registry.register("other", "其他工具", {"type": "object"}, lambda args: "done")

    adapter = MockLLMAdapter([
        ("", [tool_call("trigger_stop", "{}"), tool_call("other", "{}")]),
        ("完成", []),
    ])
    loop, kernel, _ = _make_loop(tmp_path, adapter, registry)
    loop.abort_event = asyncio.Event()
    loop_ref["loop"] = loop

    result, _ = await loop.run_task("跑触发停止", system_prompt=SYSTEM_PROMPT)
    assert "Stopped" in result
    # 第二个工具不应被执行
    tool_msgs = [m for m in kernel.ctx.messages if m.role == "tool"]
    assert all("trigger_stop" in m.content or m.name == "trigger_stop" for m in tool_msgs)


async def test_before_tool_cancel(tmp_path):
    """beforeTool 中间件可阻断工具执行。"""
    registry = ToolRegistry()
    registry.register("danger", "危险工具", {"type": "object"},
                      lambda args: "[Success] 不该执行")

    adapter = MockLLMAdapter([
        ("", [tool_call("danger", "{}")]),
        ("完成", []),
    ])
    loop, kernel, _ = _make_loop(tmp_path, adapter, registry)

    @kernel.before_tool.use
    async def block(ctx, data, next):
        data["cancel"] = True
        data["reason"] = "被测试拦截"
        return await next(data)

    await loop.run_task("跑危险工具", system_prompt=SYSTEM_PROMPT)
    tool_msgs = [m for m in kernel.ctx.messages if m.role == "tool"]
    assert "Tool Execution Cancelled" in tool_msgs[0].content
    assert "被测试拦截" in tool_msgs[0].content
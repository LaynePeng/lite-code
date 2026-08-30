"""AgentLoop 主循环测试（第14课 + 第2课增强：自愈/死循环/截断/停止/会话落盘）。"""
import asyncio
import os

import pytest

from litecode.core.agent_loop import AgentLoop
from litecode.core.kernel import Kernel
from litecode.core.session_store import SessionStore
from litecode.core.types import Message
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
                      lambda args: "A" * 60000)

    adapter = MockLLMAdapter([
        ("", [tool_call("big_tool", "{}")]),
        ("完成", []),
    ])
    loop, kernel, _ = _make_loop(tmp_path, adapter, registry)

    await loop.run_task("跑大输出", system_prompt=SYSTEM_PROMPT)
    tool_msgs = [m for m in kernel.ctx.messages if m.role == "tool"]
    assert tool_msgs and len(tool_msgs[0].content) < 50000
    assert "truncated" in tool_msgs[0].content


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


# ---------------------------------------------------------------- 上下文压缩（opencode 风格）


def test_effective_cap_window_proportional():
    """大窗口 → 90%×窗口（不被 48K 预算锁死）；小窗口 → min(预算, 90%×窗口) 兜底。"""
    registry = ToolRegistry()
    adapter = MockLLMAdapter([("完成", [])])
    kernel = Kernel("cap-test")
    loop = AgentLoop(kernel=kernel, adapter=adapter, registry=registry,
                     token_budget=48_000, context_window=1_000_000)
    assert loop._effective_cap() == 900_000

    loop2 = AgentLoop(kernel=kernel, adapter=adapter, registry=registry,
                      token_budget=48_000, context_window=128_000)
    assert loop2._effective_cap() == 115_200

    loop3 = AgentLoop(kernel=kernel, adapter=adapter, registry=registry,
                      token_budget=48_000, context_window=40_000)
    assert loop3._effective_cap() == 36_000  # 小窗口保持 min(预算, 90%×窗口)


class SummarizingAdapter(MockLLMAdapter):
    """无工具调用 → 返回摘要（模拟压缩器）；有工具调用 → 走脚本化响应。"""

    async def chat_stream(self, messages, tools, events=None):
        if not tools:
            return "已完成的任务摘要：修改了 a.txt，结论正确。", [], None
        return await super().chat_stream(messages, tools, events)


async def test_try_compact_replaces_head_with_summary():
    """超预算时：旧轮次被 LLM 摘要替换，最近轮次原样保留，前缀只失效一次。"""
    registry = ToolRegistry()
    adapter = SummarizingAdapter([("完成", [])])
    kernel = Kernel("compact-test")
    loop = AgentLoop(kernel=kernel, adapter=adapter, registry=registry,
                     token_budget=200, context_window=1_000_000)

    messages = [Message(role="system", content="你是测试 Agent。")]
    for i in range(3):
        messages.append(Message(role="user", content=f"问题{i} " + "x" * 150))
        messages.append(Message(role="assistant", content=f"回答{i} " + "x" * 150))

    # 直接构造超预算场景：hard_cap 设为 100 强制压缩
    plan = loop.context_manager.split_for_compaction(messages, hard_cap=100)
    assert plan is not None
    head, tail, head_tokens = plan
    assert any(m.content.startswith("问题0") for m in head)
    assert any(m.content.startswith("问题2") for m in tail)
    assert head_tokens > 0

    compacted = await loop._try_compact(messages, 100)
    assert compacted is not None
    assert compacted[0].role == "system"
    assert compacted[1].role == "user" and "历史摘要" in compacted[1].content
    assert any(m.content.startswith("问题2") for m in compacted)
    assert not any(m.content.startswith("问题0") for m in compacted)
    assert loop._compression_count == 1
    assert loop._compressed_tokens > 0


async def test_summarize_failure_falls_back_none():
    """摘要调用失败 → 返回 None，调用方回退旧裁剪。"""
    registry = ToolRegistry()

    class BrokenAdapter(MockLLMAdapter):
        async def chat_stream(self, messages, tools, events=None):
            if not tools:
                raise RuntimeError("摘要失败")
            return await super().chat_stream(messages, tools, events)

    adapter = BrokenAdapter([("完成", [])])
    kernel = Kernel("compact-fail")
    loop = AgentLoop(kernel=kernel, adapter=adapter, registry=registry,
                     token_budget=100, context_window=1_000_000)
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="问题一"),
        Message(role="assistant", content="回答一"),
        Message(role="user", content="问题二"),
        Message(role="assistant", content="回答二"),
    ]
    result = await loop._try_compact(messages, 100)
    assert result is None
    assert loop._compression_count == 0
"""todo_write 工具测试：校验 / 存储 / 事件推送 / Agent 裁剪。"""
import asyncio

import pytest

from litecode.tools.todos import TodoPlugin, current_session_id


class _EventBus:
    """捕获事件的假总线（与 kernel.events.emit 同签名）。"""

    def __init__(self):
        self.events = []

    async def emit(self, name, payload):
        self.events.append((name, payload))


def test_todo_write_validates_and_emits():
    plugin = TodoPlugin()
    bus = _EventBus()
    plugin.bind("s1", bus)
    current_session_id.set("s1")

    result = asyncio.run(plugin.execute("todo_write", {
        "todos": [
            {"content": "调研代码结构", "status": "completed"},
            {"content": "实现功能", "status": "in_progress"},
            {"content": "补测试", "status": "pending"},
        ],
    }))

    assert "[Error]" not in result
    assert "共 3 项" in result
    # 看板状态已存储
    assert len(plugin.get("s1")) == 3
    assert plugin.get("s1")[0]["status"] == "completed"
    # 事件已推送
    assert bus.events == [("todo:updated", {"todos": plugin.get("s1")})]


def test_todo_write_rejects_bad_status_and_empty_content():
    plugin = TodoPlugin()
    current_session_id.set("s2")
    bad = asyncio.run(plugin.execute("todo_write", {
        "todos": [{"content": "x", "status": "doing"}],
    }))
    assert "[Error]" in bad and "status 非法" in bad

    empty = asyncio.run(plugin.execute("todo_write", {
        "todos": [{"content": "  ", "status": "pending"}],
    }))
    assert "[Error]" in empty and "content" in empty

    not_list = asyncio.run(plugin.execute("todo_write", {"todos": "x"}))
    assert "[Error]" in not_list


def test_todo_write_full_replace_and_bound_events():
    """全量覆盖语义：第二次调用替换第一次；未绑定的会话只存储不推送。"""
    plugin = TodoPlugin()
    bus = _EventBus()
    plugin.bind("s3", bus)
    current_session_id.set("s3")

    asyncio.run(plugin.execute("todo_write", {
        "todos": [{"content": "旧项", "status": "pending"}],
    }))
    asyncio.run(plugin.execute("todo_write", {
        "todos": [{"content": "新项A", "status": "completed"},
                  {"content": "新项B", "status": "pending"}],
    }))
    assert [t["content"] for t in plugin.get("s3")] == ["新项A", "新项B"]
    assert len(bus.events) == 2  # 每次调用都推送

    # 未绑定会话：存储成功但不推送
    current_session_id.set("s4")
    r = asyncio.run(plugin.execute("todo_write", {
        "todos": [{"content": "静默项", "status": "pending"}],
    }))
    assert "[Error]" not in r
    assert plugin.get("s4")[0]["content"] == "静默项"
    assert len(bus.events) == 2  # s3 的事件数不变


def test_plan_agent_whitelist_contains_todo_write():
    from litecode.core.agent_profile import default_plan_agent, default_build_agent
    assert "todo_write" in default_plan_agent().tools
    # build 无白名单（全量），todo_write 经插件注册自然可用
    assert default_build_agent().tools is None


def test_build_registry_exposes_todo_write(tmp_path):
    from litecode.app import AgentApp
    app = AgentApp(workspace=str(tmp_path), config_dir=str(tmp_path / ".lc"))
    names = {t.name for t in app.build_registry().get_tools()}
    assert "todo_write" in names
    plan_names = {t.name for t in app.create_agent_registry("plan").get_tools()}
    assert "todo_write" in plan_names

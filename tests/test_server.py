"""服务层 API 测试：会话管理 / 聊天 SSE 流 / 审批流程 / 安全规则热更新。

注意：httpx ASGITransport 会缓冲完整响应体才返回，无法交互式消费 SSE 长连接，
因此这里使用真实 uvicorn 服务器 + 网络客户端测试（更接近生产形态）。
"""
import asyncio
import json
import os

import httpx
import pytest
import uvicorn

from litecode.app import AgentApp
from litecode.server.app import create_app
from tests.conftest import MockLLMAdapter, tool_call


@pytest.fixture
async def live_client(tmp_path):
    app = AgentApp(workspace=str(tmp_path), config_dir=str(tmp_path / ".lite-code"))
    app._mock_adapter = MockLLMAdapter([
        ("", [tool_call("write_file", '{"filePath":"x.txt","content":"hello"}', cid="c1")]),
        ("完成", []),
    ])
    fast_app = create_app(app, token=None)
    config = uvicorn.Config(fast_app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    # lifespan 内含 models.dev 网络同步（失败静默降级），启动可能被拖慢到 ~10s
    for _ in range(400):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn 未在预期时间内启动"
    port = server.servers[0].sockets[0].getsockname()[1]

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=15) as c:
        yield c, app, server

    server.should_exit = True
    await asyncio.wait_for(server_task, timeout=10)


async def _consume_sse(stream, on_event=None):
    """消费 SSE 文本流，可选回调（用于流内审批）。"""
    events = []
    async for chunk in stream.aiter_text():
        for line in chunk.split("\n"):
            line = line.strip()
            if line.startswith("data: [DONE]"):
                return events
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                events.append(ev)
                if on_event:
                    await on_event(ev)
    return events


async def test_status_and_sessions(live_client):
    c, app, _ = live_client
    r = await c.get("/api/status")
    assert r.status_code == 200
    assert r.json()["version"] == "0.7.0rc0"

    r = await c.post("/api/sessions", json={"name": "会话A"})
    assert r.status_code == 200
    sid = r.json()["session_id"]

    r = await c.get("/api/sessions")
    assert any(s["session_id"] == sid for s in r.json())
    r = await c.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200
    r = await c.get("/api/sessions")
    assert all(s["session_id"] != sid for s in r.json())


async def test_chat_sse_flow(live_client):
    c, app, _ = live_client
    r = await c.post("/api/sessions", json={"name": "s"})
    sid = r.json()["session_id"]

    r = await c.post("/api/chat", json={"session_id": sid, "prompt": "创建文件"})
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    async with c.stream("GET", f"/api/tasks/{task_id}/events") as stream:
        events = await _consume_sse(stream)

    types = [e["type"] for e in events]
    assert "task:start" in types
    assert "llm:stream" in types
    assert "message:added" in types
    assert "context:stats" in types
    assert any(e["type"] == "tool:before_execute" and e["data"]["toolName"] == "write_file"
               for e in events)
    assert types[-1] == "task:done"
    done = next(e for e in events if e["type"] == "task:done")
    assert done["data"]["content"] == "完成"

    # 上下文统计事件：任务内 + 会话累计
    ctx = next(e for e in events if e["type"] == "context:stats")
    assert ctx["data"]["context_window"] == 1_000_000
    assert ctx["data"]["task"]["prompt_tokens"] >= 0
    assert ctx["data"]["session"]["prompt_tokens"] >= 0

    # 会话累计接口可查
    r = await c.get(f"/api/context/stats?session_id={sid}")
    assert r.json()["session"]["prompt_tokens"] >= 0

    # 会话已持久化，且文件真实写入
    r = await c.get(f"/api/sessions/{sid}")
    assert len(r.json()["messages"]) >= 5
    with open(os.path.join(app.workspace, "x.txt"), encoding="utf-8") as f:
        assert f.read() == "hello"


async def test_multiturn_history_preserved(live_client):
    """多轮对话：第二轮不能覆盖第一轮，标题保持为第一个问题。"""
    c, app, _ = live_client
    r = await c.post("/api/sessions", json={})
    sid = r.json()["session_id"]

    async def _chat(prompt):
        r = await c.post("/api/chat", json={"session_id": sid, "prompt": prompt})
        task_id = r.json()["task_id"]
        async with c.stream("GET", f"/api/tasks/{task_id}/events") as stream:
            await _consume_sse(stream)

    await _chat("第一个问题")
    await _chat("第二个问题")

    r = await c.get(f"/api/sessions/{sid}")
    snap = r.json()
    user_msgs = [m["content"] for m in snap["messages"] if m["role"] == "user"]
    assert user_msgs == ["第一个问题", "第二个问题"], user_msgs
    # 首个 user 消息仍在，说明历史没有被第二轮覆盖
    assert any(m["role"] == "assistant" and m.get("content") for m in snap["messages"])

    r = await c.get("/api/sessions")
    entry = next(s for s in r.json() if s["session_id"] == sid)
    assert entry["title"] == "第一个问题", entry["title"]


async def test_stop_task(live_client):
    c, app, _ = live_client
    r = await c.post("/api/sessions", json={"name": "s"})
    sid = r.json()["session_id"]
    r = await c.post("/api/chat", json={"session_id": sid, "prompt": "创建文件"})
    task_id = r.json()["task_id"]

    r = await c.post(f"/api/tasks/{task_id}/stop")
    assert r.status_code == 200

    async with c.stream("GET", f"/api/tasks/{task_id}/events") as stream:
        events = await _consume_sse(stream)
    types = [e["type"] for e in events]
    assert types[-1] == "task:done" or types[-1] == "task:error"


async def test_approval_flow(live_client):
    """中危命令 → approval:request → 流内用户批准 → approval:resolved → 工具执行。"""
    c, app, _ = live_client
    app._mock_adapter = MockLLMAdapter([
        ("", [tool_call("execute_command", '{"command":"rm temp.txt"}', cid="c2")]),
        ("删好了。", []),
    ])

    r = await c.post("/api/sessions", json={"name": "s2"})
    sid = r.json()["session_id"]
    r = await c.post("/api/chat", json={"session_id": sid, "prompt": "删除文件"})
    task_id = r.json()["task_id"]

    resolved_ids = []

    async def _on_event(ev):
        if ev["type"] == "approval:request":
            apv_id = ev["data"]["id"]
            resp = await c.post("/api/approve", json={"approval_id": apv_id, "approved": True})
            assert resp.status_code == 200
            resolved_ids.append(apv_id)

    async with c.stream("GET", f"/api/tasks/{task_id}/events") as stream:
        events = await _consume_sse(stream, on_event=_on_event)

    assert resolved_ids, "应产生审批请求并被批准"
    assert any(e["type"] == "approval:resolved" and e["data"]["approved"] for e in events)
    assert any(e["type"] == "tool:before_execute"
               and e["data"]["toolName"] == "execute_command" for e in events)
    assert events[-1]["type"] == "task:done"

    # 已 resolve 的审批再次提交应 404
    r = await c.post("/api/approve", json={"approval_id": resolved_ids[0], "approved": True})
    assert r.status_code == 404


async def test_security_hot_reload(live_client):
    c, app, _ = live_client
    r = await c.get("/api/security")
    assert r.status_code == 200
    rules = r.json()
    assert "high_risk_patterns" in rules

    r = await c.post("/api/security", json={"rules": {
        **rules,
        "high_risk_patterns": [r"\bcustom-block\b"],
    }})
    assert r.status_code == 200
    assert app.guard.check_shell_command("custom-block xyz").level.value == "HIGH"


async def test_token_auth(tmp_path):
    app = AgentApp(workspace=str(tmp_path), config_dir=str(tmp_path / "cfg"))
    fast_app = create_app(app, token="secret123")
    config = uvicorn.Config(fast_app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
        r = await c.get("/api/status")
        assert r.status_code == 401
        r = await c.get("/api/status", headers={"Authorization": "Bearer secret123"})
        assert r.status_code == 200
        r = await c.get("/api/status", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    server.should_exit = True
    await asyncio.wait_for(server_task, timeout=10)
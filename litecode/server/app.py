"""FastAPI 服务：REST API + SSE 事件流 + 静态前端托管 + 可选 Bearer 鉴权。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..app import AgentApp
from .tasks import TaskManager

logger = logging.getLogger("litecode.server")

VERSION = "0.1.0"


# ---------------------------------------------------------------- 请求模型


class ChatRequest(BaseModel):
    session_id: str
    prompt: str
    task_id: Optional[str] = None


class StopRequest(BaseModel):
    task_id: str


class ApproveRequest(BaseModel):
    approval_id: str
    approved: bool


class SessionCreateRequest(BaseModel):
    name: Optional[str] = None


class SecurityUpdateRequest(BaseModel):
    rules: Dict[str, Any]


class ConfigUpdateRequest(BaseModel):
    updates: Dict[str, Any]


class LLMConfigRequest(BaseModel):
    active: Optional[str] = None
    providers: Optional[Dict[str, Dict[str, Any]]] = None


class LLMTestRequest(BaseModel):
    provider_id: Optional[str] = None
    overrides: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------- 鉴权


class TokenAuth:
    def __init__(self, token: Optional[str]) -> None:
        self.token = token

    def check(self, request: Request) -> Optional[JSONResponse]:
        if not self.token:
            return None
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {self.token}":
            return None
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)


# ---------------------------------------------------------------- 应用工厂


def _session_title(snapshot: dict) -> str:
    """从会话消息推导标题：优先元数据 name，其次首条用户消息。"""
    meta = snapshot.get("metadata") or {}
    if meta.get("name"):
        return meta["name"]
    for m in snapshot.get("messages", []):
        if m.get("role") == "user":
            text = (m.get("content") or "").strip().replace("\n", " ")
            return text[:40] or "未命名会话"
    return "未命名会话"


def create_app(app: AgentApp, token: Optional[str] = None) -> FastAPI:
    fast_app = FastAPI(title="lite-code", version=VERSION)
    auth = TokenAuth(token)
    tasks = TaskManager(app)

    fast_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _check_auth(request: Request) -> None:
        denied = auth.check(request)
        if denied is not None:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # ------------------------------------------------------------ 状态与配置

    @fast_app.get("/api/status")
    async def status(request: Request):
        _check_auth(request)
        llm_active = app.llm_registry.active
        settings = app.llm_registry.get_active_provider_settings()
        return {
            "version": VERSION,
            "workspace": app.workspace,
            "model": settings.get("model", ""),
            "base_url": settings.get("base_url", ""),
            "active_provider": llm_active,
            "api_key_configured": bool(settings.get("api_key")),
            "active_tasks": tasks.active_count(),
            "sessions_count": len(app.session_store.list()),
            "token_auth": bool(token),
        }

    @fast_app.get("/api/config")
    async def get_config(request: Request):
        _check_auth(request)
        return {
            k: app.config.get(k) for k in (
                "max_steps", "token_budget", "tool_timeout",
                "auto_approve", "pricing",
            )
        }

    @fast_app.post("/api/config")
    async def update_config(payload: ConfigUpdateRequest, request: Request):
        _check_auth(request)
        app.save_config(payload.updates)
        return {"ok": True}

    # ------------------------------------------------------------ LLM 配置

    @fast_app.get("/api/llm/providers")
    async def llm_providers(request: Request):
        _check_auth(request)
        return app.llm_provider_meta()

    @fast_app.get("/api/llm/config")
    async def llm_config(request: Request):
        _check_auth(request)
        return app.get_llm_config()

    @fast_app.post("/api/llm/config")
    async def update_llm_config(payload: LLMConfigRequest, request: Request):
        _check_auth(request)
        return app.update_llm_config(
            active=payload.active,
            providers=payload.providers,
        )

    @fast_app.post("/api/llm/test")
    async def test_llm(payload: LLMTestRequest, request: Request):
        _check_auth(request)
        result = await app.test_llm(
            provider_id=payload.provider_id or app.llm_registry.active,
            overrides=payload.overrides,
        )
        return result

    # ------------------------------------------------------------ 安全规则

    @fast_app.get("/api/security")
    async def get_security(request: Request):
        _check_auth(request)
        return app.guard.to_dict()

    @fast_app.post("/api/security")
    async def update_security(payload: SecurityUpdateRequest, request: Request):
        _check_auth(request)
        app.update_security_rules(payload.rules)
        return {"ok": True}

    # ------------------------------------------------------------ 会话管理

    @fast_app.get("/api/sessions")
    async def list_sessions(request: Request):
        _check_auth(request)
        snapshots = app.session_store.list()
        return [
            {
                "session_id": s.get("session_id"),
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "message_count": len(s.get("messages", [])),
                "title": _session_title(s),
                "metadata": s.get("metadata", {}),
            }
            for s in snapshots
        ]

    @fast_app.post("/api/sessions")
    async def create_session(payload: SessionCreateRequest, request: Request):
        _check_auth(request)
        import time as _time

        session_id = f"session_{int(_time.time() * 1000)}"
        app.session_store.save(session_id, [], {"name": payload.name or session_id})
        return {"session_id": session_id}

    @fast_app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request):
        _check_auth(request)
        snap = app.session_store.load(session_id)
        if snap is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return snap.to_dict()

    @fast_app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, request: Request):
        _check_auth(request)
        ok = app.session_store.delete(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"ok": True}

    # ------------------------------------------------------------ 工具与工作区

    @fast_app.get("/api/tools")
    async def list_tools(request: Request):
        _check_auth(request)
        registry = app.build_registry()
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in registry.get_tools()
        ]

    @fast_app.get("/api/workspace/tree")
    async def workspace_tree(depth: int = 3, request: Request = None):
        if request:
            _check_auth(request)
        from ..tools.filesystem import FileSystemTools

        fs = FileSystemTools(app.workspace)
        return {"workspace": app.workspace, "tree": fs._file_tree({"maxDepth": depth})}

    # ------------------------------------------------------------ 聊天任务

    @fast_app.post("/api/chat")
    async def chat(payload: ChatRequest, request: Request):
        _check_auth(request)
        session_id = payload.session_id.strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        prompt = payload.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")

        handle = tasks.start(session_id, prompt)
        return {"task_id": handle.task_id}

    @fast_app.get("/api/tasks/{task_id}/events")
    async def task_events(task_id: str, request: Request):
        _check_auth(request)
        handle = tasks.get(task_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        async def _stream():
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(handle.queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if item is None:
                        yield "data: [DONE]\n\n"
                        break
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                # 客户端断连：任务仍可继续，后台任务会保留状态，不在此清理
                try:
                    if not handle.queue.empty() and not handle.done:
                        handle.queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
                raise
            finally:
                # 仅在任务真正结束时（已收到 [DONE]）清理，避免断线重连 404
                if handle.done and handle.queue.empty():
                    tasks.cleanup(task_id)

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @fast_app.post("/api/tasks/{task_id}/stop")
    async def stop_task(task_id: str, request: Request):
        _check_auth(request)
        ok = tasks.stop(task_id)
        if not ok:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"ok": True}

    # ------------------------------------------------------------ 审批

    @fast_app.post("/api/approve")
    async def approve(payload: ApproveRequest, request: Request):
        _check_auth(request)
        ok = app.approval_gate.resolve(payload.approval_id, payload.approved, by="user")
        if not ok:
            raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
        return {"ok": True, "approved": payload.approved}

    # ------------------------------------------------------------ 静态前端

    dist_dir = _resolve_dist_dir()
    if os.path.isdir(dist_dir):
        @fast_app.get("/")
        async def index(request: Request):
            return await _serve_index(dist_dir)

        @fast_app.get("/{path:path}")
        async def spa_fallback(path: str, request: Request):
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            file_path = os.path.join(dist_dir, path)
            if os.path.isfile(file_path):
                from fastapi.responses import FileResponse

                return FileResponse(file_path)
            return await _serve_index(dist_dir)

    return fast_app


async def _serve_index(dist_dir: str):
    from fastapi.responses import FileResponse

    index_path = os.path.join(dist_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "lite-code 后端已就绪（前端未构建，运行 npm run build:web）"},
                        status_code=200)


def _resolve_dist_dir() -> str:
    """定位前端构建产物目录，兼容 PyInstaller 打包环境。"""
    candidates = []
    # PyInstaller: 前端资源通过 --add-data 打入 _MEIPASS
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(os.path.join(bundle_dir, "web", "dist"))
    # 开发/源码环境
    candidates.append(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "web", "dist",
        )
    )
    for c in candidates:
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, "index.html")):
            return c
    return candidates[-1]
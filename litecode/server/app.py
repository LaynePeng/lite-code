"""FastAPI 服务：REST API + SSE 事件流 + 静态前端托管 + 可选 Bearer 鉴权。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..app import AgentApp
from .tasks import TaskManager

logger = logging.getLogger("litecode.server")

VERSION = __version__


# ---------------------------------------------------------------- 请求模型


class ChatRequest(BaseModel):
    session_id: str
    prompt: str
    task_id: Optional[str] = None
    agent_id: Optional[str] = None


class StopRequest(BaseModel):
    task_id: str


class ApproveRequest(BaseModel):
    approval_id: str
    approved: bool


class SessionCreateRequest(BaseModel):
    name: Optional[str] = None
    workspace: Optional[str] = None


class SecurityUpdateRequest(BaseModel):
    rules: Dict[str, Any]


class MCPServersUpdateRequest(BaseModel):
    servers: Dict[str, Any]


class ConfigUpdateRequest(BaseModel):
    updates: Dict[str, Any]


class WorkspaceUpdateRequest(BaseModel):
    path: str


class SessionModelRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None


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
    """从会话消息推导标题：优先用户自定义 name，其次首条用户消息，空会话兜底。"""
    meta = snapshot.get("metadata") or {}
    if meta.get("name"):
        return meta["name"]
    for m in snapshot.get("messages", []):
        if m.get("role") == "user":
            text = (m.get("content") or "").strip().replace("\n", " ")
            return text[:40] or "未命名会话"
    return "新会话"


def create_app(app: AgentApp, token: Optional[str] = None) -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_fast_app: FastAPI):
        # models.dev 元数据同步（失败静默降级到内置静态表）
        import asyncio as _asyncio

        try:
            await _asyncio.to_thread(app.refresh_model_meta)
        except Exception:
            pass
        await app.mcp_manager.start()
        yield

    fast_app = FastAPI(title="lite-code", version=VERSION, lifespan=_lifespan)
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

    def _require_workspace() -> str:
        if not app.workspace:
            raise HTTPException(status_code=409, detail="请先打开项目后再创建会话或执行任务")
        return app.workspace

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
                "auto_approve", "pricing", "context_full_turns", "llm_timeout",
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

    @fast_app.get("/api/context/stats")
    async def context_stats(session_id: str = "", request: Request = None):
        if request:
            _check_auth(request)
        if not session_id:
            return {"session": {}}
        return {"session": app.get_context_session_stats(session_id)}

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

    # ------------------------------------------------------------ MCP Server 配置

    @fast_app.get("/api/mcp")
    async def mcp_status(request: Request):
        _check_auth(request)
        return app.mcp_status()

    @fast_app.post("/api/mcp")
    async def update_mcp(payload: MCPServersUpdateRequest, request: Request):
        _check_auth(request)
        # 任务运行时工具集不可热变（运行中任务的 registry 已装配完成）
        if tasks.active_count() > 0:
            raise HTTPException(status_code=409, detail="当前有任务运行，请等待任务结束后再更新 MCP 配置")
        status = await app.update_mcp_servers(payload.servers)
        return {"ok": True, **status}

    # ------------------------------------------------------------ 会话管理

    @fast_app.get("/api/sessions")
    async def list_sessions(request: Request, workspace: str = ""):
        _check_auth(request)
        snapshots = app.session_store.list()
        result = []
        for s in snapshots:
            # 按 workspace 过滤：session 的 metadata.workspace 与当前 workspace 匹配
            s_ws = (s.get("metadata") or {}).get("workspace", "")
            # 旧版本 session 没有 workspace 元数据：保留可见，避免升级后历史消失。
            if workspace and s_ws and os.path.abspath(s_ws) != os.path.abspath(workspace):
                continue
            messages = s.get("messages", [])
            if not any(m.get("role") == "user" for m in messages):
                # 列表接口必须是纯读取；创建与首条消息之间存在短暂空窗。
                continue
            result.append({
                "session_id": s.get("session_id"),
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "message_count": len(messages),
                "title": _session_title(s),
                "metadata": s.get("metadata", {}),
            })
        return result

    @fast_app.post("/api/sessions")
    async def create_session(payload: SessionCreateRequest, request: Request):
        _check_auth(request)
        _require_workspace()

        # 毫秒时间戳在快速连续创建会话时会碰撞，导致新会话覆盖旧会话。
        session_id = f"session_{uuid.uuid4().hex}"
        # metadata 记录 workspace，用于按项目过滤会话列表；name 仅在显式提供时保存
        metadata: Dict[str, Any] = {}
        if payload.name:
            metadata["name"] = payload.name
        workspace = payload.workspace or app.workspace
        if workspace:
            metadata["workspace"] = workspace
        app.session_store.save(session_id, [], metadata)
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

    @fast_app.get("/api/sessions/{session_id}/model")
    async def get_session_model(session_id: str, request: Request):
        _check_auth(request)
        snapshot = app.session_store.load(session_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        override = snapshot.metadata.get("model")
        default = app.llm_registry.get_active_provider_settings()
        if not isinstance(override, dict):
            override = None
        return {
            "override": override,
            "effective": {
                "provider": override.get("provider") if override else app.llm_registry.active,
                "model": override.get("model") if override else default.get("model", ""),
            },
        }

    @fast_app.post("/api/sessions/{session_id}/model")
    async def set_session_model(session_id: str, payload: SessionModelRequest, request: Request):
        _check_auth(request)
        snapshot = app.session_store.load(session_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        metadata = dict(snapshot.metadata)
        if not payload.provider and not payload.model:
            metadata.pop("model", None)
            override = None
        else:
            provider = (payload.provider or "").strip()
            model = (payload.model or "").strip()
            settings = app.llm_registry.providers.get(provider)
            configured_models = (settings or {}).get("models") or []
            if not provider or not model or not settings or not settings.get("api_key"):
                raise HTTPException(status_code=400, detail="只能选择已配置 API Key 的供应商和模型")
            if model not in configured_models:
                raise HTTPException(status_code=400, detail="只能选择该供应商已配置的模型")
            override = {"provider": provider, "model": model}
            metadata["model"] = override
        app.session_store.save(session_id, snapshot.messages, metadata)
        default = app.llm_registry.get_active_provider_settings()
        return {"override": override, "effective": {
            "provider": override["provider"] if override else app.llm_registry.active,
            "model": override["model"] if override else default.get("model", ""),
        }}

    # ------------------------------------------------------------ 工具与工作区

    @fast_app.get("/api/tools")
    async def list_tools(request: Request):
        _check_auth(request)
        _require_workspace()
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

        workspace = _require_workspace()
        fs = FileSystemTools(workspace)
        return {"workspace": workspace, "tree": fs._file_tree({"maxDepth": depth})}

    @fast_app.get("/api/workspace/tree-json")
    async def workspace_tree_json(path: str = "", request: Request = None):
        """结构化目录树（侧边栏文件页签）：按路径懒加载 + git 状态字母。"""
        if request:
            _check_auth(request)
        from .tree import list_tree

        workspace = _require_workspace()
        rel = path.strip().lstrip("/\\") or ""
        try:
            data = await asyncio.to_thread(list_tree, workspace, rel)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "workspace": workspace,
            "path": rel,
            "git": {"branch": data["branch"], "has_repo": data["has_repo"]},
            "entries": data["entries"],
        }

    # ------------------------------------------------------------ Agent 配置

    @fast_app.get("/api/agents")
    async def list_agents(request: Request):
        _check_auth(request)
        return app.agents_meta()

    # ------------------------------------------------------------ 工作区

    @fast_app.post("/api/workspace")
    async def set_workspace(payload: WorkspaceUpdateRequest, request: Request):
        _check_auth(request)
        import os as _os
        path = _os.path.abspath(_os.path.expanduser(payload.path))
        if not _os.path.isdir(path):
            raise HTTPException(status_code=400, detail=f"目录不存在: {path}")
        if tasks.active_count() > 0:
            raise HTTPException(status_code=409, detail="当前有任务运行，请停止或等待任务结束后再切换项目")
        app.workspace = path
        return {"ok": True, "workspace": app.workspace}

    @fast_app.get("/api/fs/list")
    async def fs_list(path: str = "", request: Request = None):
        """浏览任意目录（用于「打开项目」目录树选择）。"""
        if request:
            _check_auth(request)
        import os as _os

        base = _os.path.expanduser(path) if path else (app.workspace or _os.path.expanduser("~"))
        base = _os.path.abspath(base)
        if not _os.path.isdir(base):
            raise HTTPException(status_code=400, detail=f"目录不存在: {base}")

        dirs, files = [], []
        try:
            entries = _os.listdir(base)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法读取: {exc}")

        for name in entries:
            full = _os.path.join(base, name)
            try:
                if _os.path.isdir(full):
                    dirs.append(name)
                else:
                    files.append(name)
            except OSError:
                continue

        def key(n: str):
            return n.lower()

        dirs.sort(key=key)
        files.sort(key=key)
        cap = 500
        return {
            "path": base,
            "parent": _os.path.dirname(base) if base != _os.path.sep else None,
            "home": _os.path.expanduser("~"),
            "is_workspace": base == app.workspace,
            "dirs": dirs[:cap],
            "files": files[:cap],
            "truncated": len(dirs) + len(files) > cap,
        }

    @fast_app.get("/api/fs/read")
    async def fs_read(path: str, request: Request = None):
        """读取工作区内的文件，返回内容、语言、行数和 git diff。"""
        if request:
            _check_auth(request)
        import os as _os

        workspace = _require_workspace()
        target = _os.path.abspath(_os.path.join(workspace, path))
        if not (target == workspace or target.startswith(workspace + _os.sep)):
            raise HTTPException(status_code=403, detail="路径越界")
        if not _os.path.isfile(target):
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

        with open(target, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        lines = content.split("\n")
        ext = _os.path.splitext(path)[1].lower()

        # 简单语言检测
        lang_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
            ".jsx": "jsx", ".go": "go", ".rs": "rust", ".java": "java",
            ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
            ".css": "css", ".scss": "scss", ".less": "less",
            ".html": "html", ".htm": "html", ".xml": "xml", ".json": "json",
            ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
            ".md": "markdown", ".txt": "text", ".sh": "bash", ".bash": "bash",
            ".zsh": "bash", ".sql": "sql", ".rb": "ruby",
            ".swift": "swift", ".kt": "kotlin", ".svelte": "svelte",
            ".vue": "vue", ".astro": "astro",
        }
        language = lang_map.get(ext, "")

        # 获取 git diff（工作区 vs HEAD）
        diff_text = ""
        try:
            proc = __import__("subprocess").run(
                ["git", "-C", workspace, "diff", "HEAD", "--", path],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10,
            )
            if proc.returncode == 0:
                diff_text = proc.stdout
        except Exception:
            pass

        return {
            "path": path,
            "content": content,
            "language": language,
            "lines": len(lines),
            "size": len(content),
            "diff": diff_text,
        }

    @fast_app.get("/api/workspace/diff")
    async def workspace_file_diff(path: str, request: Request = None):
        """获取单个文件的 git diff（工作区 vs HEAD）。"""
        if request:
            _check_auth(request)
        workspace = _require_workspace()
        try:
            proc = __import__("subprocess").run(
                ["git", "-C", workspace, "diff", "HEAD", "--", path],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10,
            )
            diff_text = proc.stdout if proc.returncode == 0 else ""
        except Exception:
            diff_text = ""
        additions = len([l for l in diff_text.split("\n") if l.startswith("+") and not l.startswith("+++")])
        deletions = len([l for l in diff_text.split("\n") if l.startswith("-") and not l.startswith("---")])
        return {"path": path, "diff": diff_text, "additions": additions, "deletions": deletions}

    # ------------------------------------------------------------ 聊天任务

    @fast_app.post("/api/chat")
    async def chat(payload: ChatRequest, request: Request):
        _check_auth(request)
        _require_workspace()
        session_id = payload.session_id.strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        prompt = payload.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")

        handle = tasks.start(session_id, prompt, agent_id=payload.agent_id)
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

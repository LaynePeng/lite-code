从本课开始，我们将进入**模块四：手写实战**。我们把前面 15 课学到的所有理论与模块组合起来，从零搭建一个名为 `lite-code` 的完整 Code Agent 桌面应用。

本课目标是构建 `lite-code` 的核心内核（Core Engine）：建立清晰的项目文件目录架构，并手写 **`TypedEventBus` 异步事件总线**、**`Pipeline` 洋葱模型中间件管道**、**`Kernel` 插件内核** 以及 **`SessionStore` 会话持久化底层**。

#### 1. 项目文件目录架构规划

首先规划 `lite-code` 的完整代码结构（Python 包 + React 前端 + Electron 外壳）：

```Plaintext
lite-code/
├── pyproject.toml              # Python 工程配置（依赖 / console 脚本）
├── litecode/                   # Python 内核与后端
│   ├── core/                   # 内核核心
│   │   ├── types.py            # 核心类型定义（数据类）
│   │   ├── events.py           # 强类型异步事件总线
│   │   ├── pipeline.py         # 洋葱模型中间件管道
│   │   ├── kernel.py           # 插件内核
│   │   ├── session_store.py    # 会话持久化与恢复
│   │   ├── token_counter.py    # Token 估算（第3课）
│   │   ├── context_manager.py  # 上下文滑动裁剪（第3课）
│   │   ├── system_prompt.py    # 静态 System Prompt 骨架（第3课）
│   │   ├── state_tracker.py    # 死循环检测（第2课）
│   │   ├── json_repair.py      # JSON 容错（第2课）
│   │   ├── truncator.py        # 输出截断（第2课）
│   │   └── agent_loop.py       # AgentLoop 主循环（第18课）
│   ├── llm/                    # LLM 多供应商适配层（第17课）
│   ├── tools/                  # 20 个内置工具 + 工具插件（第17课）
│   ├── mcp/                    # stdio MCP Client 与管理器（第11课）
│   ├── security/               # 安全沙箱（第19课）
│   ├── server/                 # FastAPI 服务 + SSE（第20课）
│   ├── orchestration/          # 子 Agent 编排（第14课）
│   ├── app.py                  # 装配层
│   └── cli.py                  # 启动入口
├── web/                        # React 前端（第20课）
└── electron/                   # Electron 桌面外壳（第21课）
```

#### 2. 定义核心类型 (`litecode/core/types.py`)

我们用 `dataclass` 统一 Agent 消息结构、ToolCall、工具定义与 Context：

```python
# litecode/core/types.py
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

Role = str  # "system" | "user" | "assistant" | "tool"

@dataclass
class ToolCall:
    id: str
    type: str = "function"
    name: str = ""
    arguments: str = ""          # ⚠️ 注意：模型返回的是 JSON 字符串碎片

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type,
                "function": {"name": self.name, "arguments": self.arguments}}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        fn = data.get("function") or {}
        return cls(id=data.get("id", ""), type=data.get("type", "function"),
                   name=fn.get("name", ""), arguments=fn.get("arguments", ""))

@dataclass
class Message:
    role: Role
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None   # role=assistant 时可能含工具调用
    tool_call_id: Optional[str] = None            # role=tool 时必须匹配调用 ID

    def to_dict(self) -> Dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data): ...

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]    # JSON Schema

@dataclass
class Context:
    session_id: str
    messages: List[Message] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    services: Dict[str, Any] = field(default_factory=dict)
```

**关键点**：
1. `ToolCall.arguments` 是模型返回的**逐字增长的 JSON 字符串碎片**，绝不能在中途 `json.loads`，必须等整个 Turn 的流接收完毕再解析（第1课结论）；
2. `Message.to_dict()` 保证序列化后与 OpenAI/DeepSeek 接口格式完全一致；
3. `Context.services` 是**依赖注入容器**（第 12 课 Cordis 思想），插件可在内核上挂载自己的服务。

#### 3. 手写强类型异步事件总线 (`litecode/core/events.py`)

基于 `asyncio` 实现事件总线，支持**异步与同步监听器**，事件名集中声明杜绝拼写错误：

```python
# litecode/core/events.py
import asyncio, logging
from typing import Any, Dict, Set

class TypedEventBus:
    """支持 async/同步监听器的强类型事件总线。"""

    EVENT_MAP: Dict[str, Any] = {
        "session:start": dict, "session:end": dict,
        "message:added": dict, "llm:stream": dict,
        "llm:turn_start": dict, "tool:before_execute": dict,
        "tool:after_execute": dict, "approval:request": dict,
        "approval:resolved": dict, "task:start": dict,
        "task:done": dict, "task:error": dict, "task:stop": dict,
        "stats:update": dict, "subagent:completed": dict,
    }

    def __init__(self) -> None:
        self._listeners: Dict[str, Set] = {}

    def on(self, event: str, listener) -> "TypedEventBus":
        self._listeners.setdefault(event, set()).add(listener)
        return self

    def off(self, event: str, listener) -> None:
        s = self._listeners.get(event)
        if s:
            s.discard(listener)

    async def emit(self, event: str, data: Any = None) -> None:
        listeners = list(self._listeners.get(event, ()))
        for listener in listeners:
            try:
                result = listener(data)
                if asyncio.iscoroutine(result):
                    await result   # 异步监听器被 await，保证事件顺序
            except Exception:
                logging.getLogger("litecode.events").exception(
                    "[EventBus] Listener error on event %s", event)
```

**增强点**：相比课程第 12 课的同步 EventEmitter，这里用 asyncio 实现，`emit` 会 **await 每个异步监听器**。这保证了「事件→SSE 推送→UI 渲染」的严格顺序，是后面 Web 端流式输出的基础。

#### 4. 手写洋葱模型中间件管道 (`litecode/core/pipeline.py`)

对应课程第 13 课的 `Pipeline` 管道，支持同步/异步中间件，`next()` 可携带更新后的数据继续传递：

```python
# litecode/core/pipeline.py
import asyncio
from typing import Any, Callable, List
from .types import Context

class Pipeline:
    """洋葱模型管道：每个中间件可决定是否调用 next() 继续向下流转。"""

    def __init__(self, name: str = "pipeline") -> None:
        self.name = name
        self._middlewares: List = []

    def use(self, middleware) -> None:
        self._middlewares.append(middleware)

    async def run(self, ctx: Context, initial_data: Any) -> Any:
        async def dispatch(index: int, data: Any) -> Any:
            if index >= len(self._middlewares):
                return data
            middleware = self._middlewares[index]

            async def next_call(next_data: Any = None) -> Any:
                return await dispatch(index + 1,
                                      next_data if next_data is not None else data)

            result = middleware(ctx, data, next_call)
            if asyncio.iscoroutine(result):
                result = await result
            return result

        return await dispatch(0, initial_data)
```

中间件签名固定为 `(ctx, data, next)`，`next(data)` 向下传递，返回 `data` 给上层——这就是第 13 课讲的**洋葱模型**：请求进入 → 逐层预处理 → 核心处理 → 逐层返回修饰。

#### 5. 手写插件内核 (`litecode/core/kernel.py`)

内核只负责维持 Context、事件总线与三阶段拦截管道，业务能力全部外包给插件（第 12 课「空间解耦」）：

```python
# litecode/core/kernel.py
import logging
from typing import Any, Dict
from .events import TypedEventBus
from .pipeline import Pipeline
from .types import Context, Message, Plugin

class Kernel:
    """空间解耦的核心：只维护 Context / 事件总线 / 三阶段拦截管道。"""

    def __init__(self, session_id: str) -> None:
        self.events = TypedEventBus()
        self.ctx = Context(session_id=session_id)
        self.before_llm = Pipeline("before_llm")     # LLM 调用前（改 Prompt）
        self.before_tool = Pipeline("before_tool")   # 工具执行前（安全审查）
        self.after_tool = Pipeline("after_tool")     # 工具执行后（结果修饰）
        self._plugins: Dict[str, Plugin] = {}

    def use(self, plugin: Plugin) -> "Kernel":
        if plugin.name in self._plugins:
            return self
        self._plugins[plugin.name] = plugin
        plugin.install(self)
        return self

    def register_service(self, name: str, service: Any) -> None:
        self.ctx.services[name] = service

    def get_service(self, name: str) -> Any:
        service = self.ctx.services.get(name)
        if service is None:
            raise KeyError(f'[Kernel] Service "{name}" is not registered.')
        return service
```

#### 6. 手写 SessionStore 会话持久化 (`litecode/core/session_store.py`)

基于 JSON 落盘，支持**原子写盘**（临时文件 + rename）防止中途崩溃产生半截 JSON：

```python
# litecode/core/session_store.py
import json, os, tempfile, time
from typing import Any, Dict, List, Optional
from .types import Message

class SessionSnapshot:
    def __init__(self, session_id, messages, metadata, created_at, updated_at):
        self.session_id = session_id
        self.messages = messages
        self.metadata = metadata
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> Dict[str, Any]:
        return {"session_id": self.session_id, "created_at": self.created_at,
                "updated_at": self.updated_at, "metadata": self.metadata,
                "messages": [m.to_dict() for m in self.messages]}

class SessionStore:
    def __init__(self, storage_dir: str = "./.lite-code/sessions") -> None:
        self.storage_dir = os.path.abspath(storage_dir)
        os.makedirs(self.storage_dir, exist_ok=True)

    def _file_path(self, session_id: str) -> str:
        safe = session_id.replace("/", "_").replace("\\", "_")
        return os.path.join(self.storage_dir, f"{safe}.json")

    def save(self, session_id, messages, metadata=None) -> None:
        existing = self.load(session_id)
        snapshot = SessionSnapshot(
            session_id=session_id, messages=messages, metadata=metadata or {},
            created_at=existing.created_at if existing else int(time.time()*1000),
            updated_at=int(time.time()*1000))
        path = self._file_path(session_id)
        fd, tmp = tempfile.mkstemp(dir=self.storage_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)     # 原子替换
        except Exception:
            if os.path.exists(tmp): os.unlink(tmp)
            raise

    def load(self, session_id) -> Optional[SessionSnapshot]: ...
    def list(self) -> List[Dict[str, Any]]: ...
    def delete(self, session_id) -> bool: ...
```

**增强点**：这里加了**原子写盘**（`os.replace`），并支持 `list()`（用于 Web UI 的会话列表）与 `delete()`。

在最终项目中，`metadata` 与消息一起持久化。更新会话时必须在已有 metadata 上合并新字段，不能用新字典覆盖它，否则会丢失工作区绑定等信息。Web UI 只在用户发送第一条消息时创建 session；没有用户消息的占位 Tab 不会写入 SessionStore。

#### 7. 工程初始化与验证

创建 Python 虚拟环境并安装依赖（`pyproject.toml`）：

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

```toml
# pyproject.toml（节选）
[project]
name = "lite-code"

requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110", "uvicorn>=0.29", "httpx>=0.27",
    "tree-sitter>=0.24", "tree-sitter-typescript>=0.23",
    "pathspec>=0.12",
]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
lite-code = "litecode.cli:main"
```

验证内核可用：

```python
from litecode.core.kernel import Kernel
from litecode.core.types import Message

k = Kernel("demo")
k.ctx.messages.append(Message(role="user", content="hello"))
print(k.ctx.messages[0].to_dict())   # {'role': 'user', 'content': 'hello'}
```

#### 本课小结

在本课中，我们为 `lite-code` 搭建了坚实的 Python Core 内核：

1. 建立了标准的 Python 项目目录结构与强类型接口定义（`types.py`）；
2. 实现了 asyncio 泛型安全的 **`TypedEventBus`**（异步监听器可被 await）；
3. 实现了包含 **`Pipeline` 洋葱模型** 与 **`Context` 容器** 的 `Kernel`；
4. 编写了具备**原子写盘**与列表/删除能力的 **`SessionStore`**。

下一次我们将开启 **第17课：LLM 多供应商适配器与核心工具集 (`lite-code` 实战第二篇)** —— 手写 httpx SSE 流式解析、多 LLM 供应商注册表，以及文件/搜索/AST/编辑/Shell/Git/审查等全套工具！

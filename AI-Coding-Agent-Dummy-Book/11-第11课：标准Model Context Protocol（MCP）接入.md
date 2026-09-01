在前面的课程中，所有的工具（代码感知、沙箱、精确编辑，包括上一课的 `load_skill`）都是我们以硬编码的形式写在 Harness 内部的。Skills 解决了"项目级工作流"的定制，但**工具本身的能力边界仍然是封闭的**——随着扩展需求越来越多（例如：PostgreSQL 数据库查询器、GitHub API 插件、Figma 插件等），如果全都打包到主进程中，会导致**系统臃肿、权限失控与代码耦合**。

2024年底由 Anthropic 提出的 **Model Context Protocol (MCP)** 已经成为行业事实上的 Agent 扩展协议标准。本课我们将为 Harness 实现一个标准的 **MCP Client**，通过 JSON-RPC 2.0 协议连接外部独立运行的 MCP Server，实现工具的动态注册、发现与远程调用。

#### 1. MCP 架构与 JSON-RPC 2.0 协议交互

MCP 采用经典的 **Client - Server 架构**，客户端与服务端可以通过 `stdio`（标准输入输出）或 `SSE` 进行通信：

```text
+-------------------------------------------------------------+
|                     Agent Harness Host                      |
|                                                             |
|   +-------------------+        +------------------------+   |
|   |   Agent Core      | <----> |       MCP Client       |   |
|   +-------------------+        +-----------+------------+   |
+--------------------------------------------|----------------+
                                              | stdio / JSON-RPC
                                              v
                              +---------------+---------------+
                              |    External MCP Server        |
                              |  (e.g., server-filesystem)   |
                              +-------------------------------+
```

核心握手与调用流程分为三步：
1. **Initialize**：握手通信，协商协议版本与 Client/Server Capability；
2. **tools/list**：动态拉取 Server 暴露的所有工具 Schema，并自动转换为 Harness 的 `ToolDefinition`；
3. **tools/call**：代理执行 Agent 发起的 Tool Call 请求。

#### 2. 手写 MCP Stdio Client 传输层

基于 Python `asyncio` 编写一个通过 Standard I/O (stdio) 通信的 `MCPClient`：

```python
# mcp/client.py
import asyncio, json, logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcp")

class MCPClient:
    """通过 stdio 与外部 MCP Server 进程通信的 JSON-RPC 2.0 客户端。"""

    def __init__(self, name: str, command: str, args: List[str] = None, env: Dict[str, str] = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self._request_id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._process: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        """启动 MCP Server 进程并建立 stdio 双向通道。"""
        self._process = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,   # 避免外部进程日志污染 stdout JSON 流
            env=self.env,
        )

        # 启动后台读取协程
        asyncio.ensure_future(self._read_stdout())

        # 1. 发送初始化请求 (JSON-RPC 2.0)
        await self.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "harness", "version": "1.0.0"},
        })

        # 2. 发送 initialized 通知
        await self.notify("notifications/initialized", {})

    async def _read_stdout(self) -> None:
        """持续读取 stdout，按行解析 JSON-RPC 响应。"""
        assert self._process and self._process.stdout
        async for chunk in self._process.stdout:
            line = chunk.decode("utf-8", errors="replace")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = msg.get("id")
            if rid and rid in self._pending:
                future = self._pending.pop(rid)
                if msg.get("error"):
                    future.set_exception(
                        Exception(f"MCP Error [{msg['error']['code']}]: {msg['error']['message']}"))
                else:
                    future.set_result(msg.get("result"))

    async def list_tools(self) -> List[Dict[str, Any]]:
        """获取远程 MCP Server 暴露的所有 Tool 定义。"""
        res = await self.request("tools/list", {})
        return (res or {}).get("tools") or []

    async def call_tool(self, name: str, args: Dict[str, Any]) -> str:
        """远程调用指定 Tool。"""
        res = await self.request("tools/call", {"name": name, "arguments": args})
        parts = []
        for item in (res or {}).get("content") or []:
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)

    async def request(self, method: str, params: dict) -> Any:
        rid = self._request_id
        self._request_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        assert self._process and self._process.stdin
        self._process.stdin.write((payload + "\n").encode())
        await self._process.stdin.drain()
        # 每个请求带独立超时：外部进程可能僵死，不能让 Agent 永远等下去
        return await asyncio.wait_for(future, timeout=30)

    async def notify(self, method: str, params: dict) -> None:
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        assert self._process and self._process.stdin
        self._process.stdin.write((payload + "\n").encode())
        await self._process.stdin.drain()

    async def close(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()          # 先温和终止
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._process.kill()           # 3s 后强杀
        self._process = None
```

#### 3. 实现工具注册表与 MCP 动态加载中心

Manager 负责**多 Server 连接管理、工具命名与路由**。真实实现是**配置驱动**的——MCP Server 列表来自配置文件，而不是硬编码：

```python
# mcp/manager.py（核心）
class MCPManager:
    """管理多个 MCP Server 连接，将外部工具动态注册到 Harness。"""

    def __init__(self, configs: Dict[str, Any]) -> None:
        self.configs = configs or {}       # 来自 config.json 的 mcp_servers 段
        self.clients: Dict[str, MCPClient] = {}
        self.routes: Dict[str, tuple[MCPClient, str]] = {}    # 暴露名 -> (client, 原始工具名)
        self.tool_defs: Dict[str, Dict[str, Any]] = {}

    async def start(self) -> None:
        for server_name, config in self.configs.items():
            if not isinstance(config, dict) or config.get("enabled") is False:
                continue
            command = config.get("command")
            if not command:
                continue
            env = {**os.environ, **{str(k): str(v) for k, v in (config.get("env") or {}).items()}}
            client = MCPClient(server_name, command, config.get("args") or [], env)
            try:
                await client.start()
                self.clients[server_name] = client
                for tool in await client.list_tools():
                    raw_name = tool.get("name", "")
                    if not raw_name:
                        continue
                    exposed = f"mcp_{server_name}_{raw_name}".replace("-", "_")
                    self.routes[exposed] = (client, raw_name)
                    self.tool_defs[exposed] = tool
            except Exception:
                logger.exception("[MCP] 启动 server 失败: %s", server_name)
                await client.close()       # 失败的连接立即回收，不影响其他 Server

    def register_tools(self, registry, allowed=None, exclude=None) -> None:
        for exposed, (client, raw_name) in self.routes.items():
            if allowed is not None and exposed not in allowed:
                continue                   # 尊重 Agent 的工具白名单（Build/Plan 裁剪）
            if exclude and exposed in exclude:
                continue
            meta = self.tool_defs.get(exposed, {})
            registry.register(
                exposed,
                f"MCP {client.name}: {meta.get('description') or raw_name}",
                meta.get("inputSchema") or {"type": "object", "properties": {}},
                lambda args, c=client, n=raw_name: c.call_tool(n, args),
            )

    async def close(self) -> None:
        for client in self.clients.values():
            await client.close()
        self.clients.clear()
        self.routes.clear()
        self.tool_defs.clear()
```

两个关键设计：

**① 工具名前缀 `mcp_<server>_<tool>`**：不同 MCP Server 可能暴露同名工具（两个 Server 都有 `query`），直接用原始名注册会互相覆盖，还可能与内置工具撞名。`server 名 + 工具名`的组合全局唯一，`-` 替换为 `_` 则保证名字能安全地进入 LLM 的 function calling 字母表。

**② 注册即裁剪**：`register_tools` 接收 `allowed`/`exclude` 参数，与内置工具走同一套 Agent 工具裁剪（第 15 课 Build/Plan）——MCP 工具不是法外之地，Plan Agent 的只读约束对它同样生效。

#### 4. 在 Harness 主控制循环中集成 MCP 工具

配置持久化在 `~/.lite-code/config.json` 的 `mcp_servers` 段：

```json
{
  "mcp_servers": {
    "sqlite": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sqlite", "./data.db"],
      "enabled": true
    }
  }
}
```

装配层（`AgentApp`）持有 Manager，在 FastAPI 生命周期钩子里完成连接握手（服务启动时、而不是导入时），构建 ToolRegistry 时把 MCP 工具注册进来：

```python
# app.py（核心）
self.mcp_manager = MCPManager(self.config.get("mcp_servers") or {})
...

def build_registry(self, allowed=None, exclude=None, permissions=None) -> ToolRegistry:
    ...
    for plugin in self.tool_plugins():
        kernel.use(plugin)
    self.mcp_manager.register_tools(registry, allowed=allowed, exclude=exclude)
    return registry

async def close(self) -> None:
    await self.mcp_manager.close()      # Core 关闭时终止所有 MCP Server 子进程
```

**安全边界：外部进程提供的工具必须过审批**。内置工具的行为是我们审计过的，而 MCP Server 是任意外部进程——它的"query"工具完全可能在背后读写文件系统。因此在安全插件的拦截管道里，所有 `mcp_*` 工具默认视为需要用户确认的操作（详见第 19 课的三级风险模型），用户批准后调用才会放行。MCP 工具与内置工具共用超时控制、事件流与 SSE 工具卡片，在 Web UI 上的体验完全一致。

### 本课小结

在本课中，我们为 Harness 赋予了连接外部 Tool 生态的无限扩展能力：

1. 深入理解了 **Model Context Protocol (MCP)** 的 JSON-RPC 2.0 帧结构；
2. 手写了基于 asyncio 的轻量 **MCP Stdio Client** 传输与握手格式（含请求级超时与温和关闭）；
3. 实现了**配置驱动**的 MCP 管理中心：动态 Server 发现、`mcp_<server>_<tool>` 命名防冲突、按 Agent 裁剪注册、统一安全销毁；
4. 确立了 MCP 工具的**安全边界**：外部进程提供的工具默认需要用户审批，与内置工具共用同一套安全管道。

下一次我们将正式开启 **第12课：Cordis 插件内核设计（Spatiotemporal Composability）** —— 探索高级 Harness 是如何通过"时空解耦"与可重写轨迹实现 Agent 行为解耦的！
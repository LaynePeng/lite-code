在前面的课程中，所有的工具（`astTools`、`sandboxTools`、`editorTools`）都是我们以硬编码的形式写在 Harness 内部的。但在工业级 Code Agent 架构中，随着扩展工具越来越多（例如：PostgreSQL 数据库查询器、GitHub API 插件、Figma 插件等），如果全都打包到主进程中，会导致**系统臃肿、权限失控与代码耦合**。

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
# mcp/mcp_client.py
import asyncio, json, logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcp")

class MCPClient:
    """通过 stdio 与外部 MCP Server 进程通信的 JSON-RPC 2.0 客户端。"""

    def __init__(self, command: str, args: List[str] = None):
        self.command = command
        self.args = args or []
        self._request_id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._buffer = ""
        self._process: Optional[asyncio.subprocess.Process] = None

    async def connect(self) -> None:
        """启动 MCP Server 进程并建立 stdio 双向通道。"""
        self._process = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # 启动后台读取协程
        asyncio.ensure_future(self._read_stdout())

        # 1. 发送初始化请求 (JSON-RPC 2.0)
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "lite-code", "version": "0.1.0"},
        })

        # 2. 发送 initialized 通知
        await self._send_notification("notifications/initialized", {})

    async def _read_stdout(self) -> None:
        """持续读取 stdout，按行解析 JSON-RPC 响应。"""
        assert self._process and self._process.stdout
        async for chunk in self._process.stdout:
            self._buffer += chunk.decode("utf-8", errors="replace")
            lines = self._buffer.split("\n")
            self._buffer = lines.pop()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
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
        res = await self._send_request("tools/list", {})
        mcp_tools = res.get("tools", [])
        # 转换为 Harness 标准格式
        return [
            ToolDefinition(name=t["name"], description=t.get("description", ""),
                           parameters=t.get("inputSchema", {}))
            for t in mcp_tools
        ]

    async def call_tool(self, name: str, args: Dict[str, Any]) -> str:
        """远程调用指定 Tool。"""
        res = await self._send_request("tools/call", {"name": name, "arguments": args})
        if isinstance(res.get("content"), list):
            return "\n".join(
                item.get("text", json.dumps(item))
                for item in res["content"]
                if item.get("type") == "text"
            )
        return json.dumps(res)

    async def _send_request(self, method: str, params: dict) -> Any:
        rid = self._request_id
        self._request_id += 1
        future = asyncio.get_event_loop().create_future()
        self._pending[rid] = future
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        assert self._process and self._process.stdin
        self._process.stdin.write((payload + "\n").encode())
        await self._process.stdin.drain()
        return await future

    async def _send_notification(self, method: str, params: dict) -> None:
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        assert self._process and self._process.stdin
        self._process.stdin.write((payload + "\n").encode())
        await self._process.stdin.drain()

    async def disconnect(self) -> None:
        if self._process:
            self._process.kill()
            await self._process.wait()
            self._process = None
```

#### 3. 实现工具注册表与 MCP 动态加载中心

```python
# mcp/mcp_manager.py
from dataclasses import dataclass
from typing import Dict, List

@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: List[str] = None

class MCPManager:
    """管理多个 MCP Server 连接，将外部工具动态注册到 Harness。"""

    def __init__(self):
        self.clients: Dict[str, MCPClient] = {}
        self.tool_to_server: Dict[str, MCPClient] = {}

    async def load_servers(self, configs: List[MCPServerConfig]) -> List[ToolDefinition]:
        all_tools: List[ToolDefinition] = []
        for config in configs:
            try:
                print(f"[MCP Manager]: Connecting to server \"{config.name}\"...")
                client = MCPClient(config.command, config.args or [])
                await client.connect()
                self.clients[config.name] = client

                tools = await client.list_tools()
                for tool in tools:
                    self.tool_to_server[tool.name] = client
                    all_tools.append(tool)

                print(f"[MCP Manager]: Loaded {len(tools)} tools from \"{config.name}\".")
            except Exception as e:
                print(f"[MCP Manager]: Failed to load server \"{config.name}\": {e}")
        return all_tools

    async def execute_tool(self, name: str, args: dict) -> str:
        client = self.tool_to_server.get(name)
        if not client:
            raise ValueError(f"No MCP Server registered for tool \"{name}\"")
        return await client.call_tool(name, args)

    def shutdown_all(self):
        for name, client in self.clients.items():
            print(f"[MCP Manager]: Shutting down server \"{name}\"")
            asyncio.ensure_future(client.disconnect())
```

#### 4. 在 Harness 主控制循环中集成 MCP 工具

```python
# main.py
import asyncio

async def main():
    mcp_manager = MCPManager()

    # 动态接入外部 MCP Server 生态
    mcp_tools = await mcp_manager.load_servers([
        MCPServerConfig(name="sqlite-server",
                        command="npx",
                        args=["-y", "@modelcontextprotocol/server-sqlite", "--db-path", "./app.db"]),
    ])

    # 将系统原生工具与 MCP 动态工具合并
    combined_tools = (
        ast_tools + editor_tools + sandbox_tools + mcp_tools
    )

    print(f"Total tools registered in Harness: {len(combined_tools)}")

    # 正常运行 Agent 循环...
    # 退出时安全销毁 MCP 子进程
    mcp_manager.shutdown_all()
```

### 本课小结

在第八课中，我们为 Harness 赋予了连接外部 Tool 生态的无限扩展能力：

1. 深入理解了 **Model Context Protocol (MCP)** 的 JSON-RPC 2.0 帧结构；
2. 手写了基于 asyncio 的轻量 **MCP Stdio Client** 传输与握手格式；
3. 实现了包含 **动态 Server 发现、工具路由映射与统一安全销毁** 的 MCP 集中管理器。

下一次我们将正式开启 **模块四：Harness 核心架构解析 (Cordis 插件系统)** —— 学习 **第九课：Cordis 插件内核设计 (Spatiotemporal Composability)**，探索高级 Harness 是如何通过"时空解耦"与可重写轨迹实现 Agent 行为解耦的！
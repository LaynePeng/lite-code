from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from .client import MCPClient

logger = logging.getLogger("litecode.mcp")


class MCPManager:
    def __init__(self, configs: Dict[str, Any]) -> None:
        self.configs = configs or {}
        self.clients: Dict[str, MCPClient] = {}
        self.routes: Dict[str, tuple[MCPClient, str]] = {}
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
                await client.close()

    def register_tools(self, registry, allowed=None, exclude=None) -> None:
        for exposed, (client, raw_name) in self.routes.items():
            if allowed is not None and exposed not in allowed:
                continue
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

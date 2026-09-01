from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional


class MCPClient:
    def __init__(self, name: str, command: str, args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.process: Optional[asyncio.subprocess.Process] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._next_id = 1

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=self.env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "lite-code", "version": "0.11.0"},
        })
        await self.notify("notifications/initialized", {})

    async def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        reader = self.process.stdout
        while True:
            line = await reader.readline()
            if not line:
                return
            try:
                message = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            req_id = message.get("id")
            if req_id in self._pending:
                future = self._pending.pop(req_id)
                if "error" in message:
                    future.set_exception(RuntimeError(str(message["error"])))
                else:
                    future.set_result(message.get("result"))

    async def _send(self, message: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP server {self.name} is not running")
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        self.process.stdin.write(payload + b"\n")
        await self.process.stdin.drain()

    async def request(self, method: str, params: Dict[str, Any]) -> Any:
        req_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return await asyncio.wait_for(future, timeout=30)

    async def notify(self, method: str, params: Dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self.request("tools/list", {})
        return (result or {}).get("tools") or []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        parts = []
        for item in (result or {}).get("content") or []:
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
        if self._reader_task:
            self._reader_task.cancel()

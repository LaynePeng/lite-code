import asyncio
import json
import sys

from litecode.mcp.client import MCPClient


async def test_mcp_stdio_tools(tmp_path):
    server = tmp_path / "server.py"
    server.write_text(
        '''import json, sys
for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "mock"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "hello", "description": "hello tool", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "hello " + req["params"]["arguments"].get("name", "")}]}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
''',
        encoding="utf-8",
    )
    client = MCPClient("mock", sys.executable, [str(server)])
    await client.start()
    try:
        tools = await client.list_tools()
        assert tools[0]["inputSchema"]["properties"]["name"]["type"] == "string"
        assert await client.call_tool("hello", {"name": "lite-code"}) == "hello lite-code"
    finally:
        await client.close()

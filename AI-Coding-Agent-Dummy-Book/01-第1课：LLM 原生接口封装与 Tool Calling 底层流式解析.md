构建 Agent Harness 的首要规则是：**不要使用 LangChain、LlamaIndex 等高层抽象框架**。高层框架封装了底层状态机，掩盖了消息流动的细节，后续无法进行细粒度的上下文控制、工具拦截与插件化解耦。

本课将使用 **Python** 纯手写实现底层 LLM API 调用机制，并从 Byte/Token 级解析大模型在流式输出（Streaming）下的 **Tool Calling 增量拼接**。

#### 1. 深入理解 Message 状态与 JSON Schema 协议

LLM 的 Tool Calling 本质上就是按照约定的 JSON Schema 返回特定格式的文本字符串。

一个标准的 Tool Calling 通信包含以下关键类型：

```python
# types.py - Harness 基础消息类型
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

Role = str  # "system" | "user" | "assistant" | "tool"

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]    # JSON Schema

@dataclass
class ToolCall:
    id: str                       # LLM 生成的唯一调用 ID（如 "call_abc123"）
    type: str = "function"
    name: str = ""
    arguments: str = ""           # ⚠️ 注意：模型返回的是 JSON 格式的字符串碎片

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type,
                "function": {"name": self.name, "arguments": self.arguments}}

@dataclass
class Message:
    role: Role
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None   # role=assistant 时可能包含工具调用请求
    tool_call_id: Optional[str] = None            # role=tool 时必须提供对应的调用 ID 进行匹配
```

#### 2. 流式输出（Streaming）下的 Tool Call 增量拼接

在工业级 Harness 中，阻塞等待 LLM 生成完整响应会带来不可接受的延迟。我们需要通过 SSE (Server-Sent Events) 逐 Token 接收流式数据。

在 Streaming 模式下，大模型会把一个 `tool_calls` 拆分成数十个 Chunk 分批吐出。例如：

```plaintext
Chunk 0: { index: 0, id: "call_99", function: { name: "read_file", arguments: "" } }
Chunk 1: { index: 0, function: { arguments: "{\"path\":" } }
Chunk 2: { index: 0, function: { arguments: " \"src/main" } }
Chunk 3: { index: 0, function: { arguments: ".ts\"}" } }
```

我们必须设计一个 **Stream Accumulator（流累加器）**，基于 `index` 索引将增量的 `arguments` 碎片完整还原。

```python
# llm_provider.py
import json
import httpx
from typing import AsyncGenerator, Dict, List, Optional, Tuple

class LLMProvider:
    """底层流式 Chat 完成器，负责监听 SSE 数据包并实时拼接 Tool Call 结构。"""

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-v4-flash"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
    ) -> AsyncGenerator[dict, None]:
        """流式调用 LLM，逐事件 yield 文本增量与 tool_calls 更新。"""
        payload = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t.__dict__} for t in tools]

        # key: tool_call 在列表里的 index
        pending_tool_calls: Dict[int, ToolCall] = {}

        async with self._client.stream(
            "POST", f"{self.base_url}/chat/completions",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            json=payload,
        ) as response:
            buffer = ""
            async for chunk in response.aiter_bytes():
                buffer += chunk.decode("utf-8", errors="replace")
                lines = buffer.split("\n")
                buffer = lines.pop()

                for line in lines:
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line == "data: [DONE]":
                        return

                    if line.startswith("data: "):
                        parsed = json.loads(line[6:])
                        delta = (parsed.get("choices") or [{}])[0].get("delta")
                        if not delta:
                            continue

                        # 1. 捕获普通的文本回答
                        if delta.get("content"):
                            yield {"type": "text_delta", "content": delta["content"]}

                        # 2. 捕获 Tool Call 增量碎片并拼接
                        if delta.get("tool_calls"):
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in pending_tool_calls:
                                    pending_tool_calls[idx] = ToolCall(
                                        id=tc.get("id", ""),
                                        name="",
                                        arguments="",
                                    )
                                target = pending_tool_calls[idx]
                                if tc.get("id"):
                                    target.id = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    target.name += fn["name"]
                                if fn.get("arguments"):
                                    target.arguments += fn["arguments"]

                            yield {
                                "type": "tool_calls_update",
                                "toolCalls": list(pending_tool_calls.values()),
                            }
```

这个累加器还有一类**更隐蔽的坑**：`id` 只随首个 chunk 出现一次，甚至**部分供应商（Kimi/GLM/通义等）在流式响应里从头到尾都不携带 `id`**。空 id 的 `assistant(tool_calls)` 发出的 tool 消息在 API 侧无法匹配，会直接报 `HTTP 400: insufficient tool messages following tool_calls message`（本课结论 1 的典型翻车现场）。因此**流收尾时必须兜底**——缺失的 id 用合成值补齐：

```python
def finalize_tool_calls(pending_tool_calls: Dict[int, ToolCall]) -> List[ToolCall]:
    calls = [pending_tool_calls[i] for i in sorted(pending_tool_calls)]
    calls = [c for c in calls if c.name]          # 丢弃从未拼出 name 的空壳
    for c in calls:
        if not c.id:                               # 供应商缺 id → 合成兜底
            c.id = f"call_{uuid.uuid4().hex[:12]}"
    return calls
```

> 实测教训（后续版本修复）：某兼容供应商的流式响应不带 `tool_call id`，用户连续两次"网上查一下"都触发 webfetch 后二次请求 400。根因不在 webfetch 工具本身，而在解析层放行了空 id——补齐合成 id 后闭环恢复。

#### 2.5 多字节 UTF-8 增量解码（中文乱码的根因）

上面的 `buffer += chunk.decode("utf-8", errors="replace")` 在中文回复下**必然出错**：`chunk` 是网络层切分的原始字节，可能把一个多字节字符**拦腰截断**。

UTF-8 中文字符通常是 3 字节（如 `看` = `E7 9C 8B`）。当 chunk 边界恰好在字符中间，比如第一个 chunk 只带 `E7`、第二个才带 `9C 8B`：

```python
chunk1 = b'\xe7'                    # 看 的第一个字节
text1 = chunk1.decode("utf-8", errors="replace")   # → "�"  ← 乱码诞生
chunk2 = b'\x9c\x8b'
text2 = chunk2.decode("utf-8", errors="replace")   # → "��" ← 又两个乱码
```

`errors="replace"` 会把残缺字节替换成 U+FFFD（``），导致 "先看根" 变成 "先�根"。

**正确做法**：做**字节级增量解码**。不要直接对每个网络 chunk 调用 `decode(errors="replace")`，而是把上次未完成的字节与本次 chunk 合并；如果解码器报告末尾字符不完整，只保留从错误位置开始的字节，等下一个 chunk 到达后再拼接。

```python
def decode_utf8_incremental(buffer: bytes, chunk: bytes):
    """增量 UTF-8 解码：避免多字节字符在 chunk 边界被截断。"""
    data = buffer + chunk
    try:
        return data.decode("utf-8"), b""   # 全部是完整字符
    except UnicodeDecodeError as exc:
        # 只有确实未完成的末尾字符才缓存；不能固定截掉最后 3 字节。
        if exc.reason == "unexpected end of data" and exc.start < len(data):
            return data[:exc.start].decode("utf-8"), data[exc.start:]
        # 中间出现非法字节时，替换该非法输入并继续，避免缓存合法文本。
        return data.decode("utf-8", errors="replace"), b""

# 在 SSE 解析中配合行缓冲使用
byte_buffer = b""
buffer = ""
async for chunk in response.aiter_bytes():
    text, byte_buffer = decode_utf8_incremental(byte_buffer, chunk)
    buffer += text
    lines = buffer.split("\n")
    buffer = lines.pop()
    # ... 后续按行解析 SSE 事件
```

这个模式对**任何**多字节编码的流式协议都通用（中文、emoji、韩文等），也值得抽取为公共工具函数放在适配器基类中，让所有供应商复用——我们在实战篇中就会这样做。

> UTF-8 解码只负责保证字节转换正确，Web UI 还需要独立保证高频增量事件的状态累积正确。前端状态管理的处理方式见第 20 课。

#### 3. 编写最简 Agent 主循环 (Agent Loop)

有了底层流式拼接器后，Agent Harness 的核心任务就是维护一个 `while` 控制循环：**Send Message → Parse Tool Calls → Execute Native Tool → Append Result → Repeat**。

```python
# agent_minimal.py
import asyncio, os, json

# 定义一个基础的本地文件读取工具
read_directory_tool = ToolDefinition(
    name="list_files",
    description="列出项目目录下的文件结构",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对路径，如 '.' 或 'src'"}
        },
        "required": ["path"],
    },
)

# 本地工具执行器 (Tool Registry Mock)
async def execute_tool(name: str, args_json: str) -> str:
    args = json.loads(args_json)
    if name == "list_files":
        return json.dumps({
            "path": args["path"],
            "files": ["pyproject.toml", "src/core/types.py", "src/agent.py", "tsconfig.json"],
        })
    raise ValueError(f"Tool not found: {name}")

async def start_minimal_agent(user_prompt: str):
    provider = LLMProvider(api_key=os.environ.get("DEEPSEEK_API_KEY", ""))
    tools = [read_directory_tool]

    messages: List[Message] = [
        Message(role="system", content="你是一个专业的软件工程 Agent Helper。"),
        Message(role="user", content=user_prompt),
    ]

    is_completed = False
    turns_count = 0
    MAX_TURNS = 5

    while not is_completed and turns_count < MAX_TURNS:
        turns_count += 1
        print(f"\n\n=== [Turn {turns_count}] ===")

        assistant_content = ""
        active_tool_calls: List[ToolCall] = []

        # 监听 LLM 的流式输出
        async for event in provider.chat_stream(messages, tools):
            if event["type"] == "text_delta":
                print(event["content"], end="", flush=True)
                assistant_content += event["content"]
            elif event["type"] == "tool_calls_update":
                active_tool_calls = event["toolCalls"]

        # 判定：如果大模型没有触发任何 Tool Call，说明它给出了最终答复
        if not active_tool_calls:
            messages.append(Message(role="assistant", content=assistant_content))
            is_completed = True
            print("\n\n[Agent 任务完成]")
            break

        # 将 Assistant 的决策（包含 Tool Call 请求）写入历史记录
        messages.append(Message(
            role="assistant",
            content=assistant_content or None,
            tool_calls=active_tool_calls,
        ))

        # 依次执行大模型请求的所有工具，并将结果填回上下文
        for call in active_tool_calls:
            print(f"\n\n[执行工具]: {call.name}({call.arguments})")
            try:
                tool_result = await execute_tool(call.name, call.arguments)
                # 关键点：role 为 tool，且必须匹配 tool_call_id
                messages.append(Message(
                    role="tool", tool_call_id=call.id, content=tool_result,
                ))
            except Exception as e:
                messages.append(Message(
                    role="tool", tool_call_id=call.id, content=f"Error: {e}",
                ))
```

### 第一课的核心结论与注意事项

1. **`tool_call_id` 强关联性**：当插入 `role: "tool"` 消息时，必须严格对应前一条 `assistant` 消息返回的 `tool_call_id`。如果 ID 丢损或错位，OpenAI/DeepSeek API 会直接抛出 HTTP 400 状态码。**更隐蔽的是"空 id"**：部分供应商流式响应不携带 id，必须像上文 `finalize_tool_calls` 那样在流收尾时补齐合成 id，否则 tool 消息同样无法匹配。
2. **Arguments 延迟解析**：流式推送过程中，`call.arguments` 是逐字增长的**非合法 JSON 串**。绝对不能在 Stream 中途调用 `json.loads`，必须等待整个 Turn 的流事件接收完毕后再解析。
3. **状态队列不可丢失**：Harness 的完整会话历史是一个由 `system` → `user` → `assistant(tool_calls)` → `tool(result)` → `assistant` 构成的严格状态链条。任何一环丢失都会破坏 LLM 的上下文认知。

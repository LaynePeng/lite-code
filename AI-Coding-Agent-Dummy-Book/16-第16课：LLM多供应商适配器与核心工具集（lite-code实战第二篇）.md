在上一课中，我们构建了 `lite-code` 的核心内核（`Kernel`、`TypedEventBus`、`Pipeline` 和 `SessionStore`）。

本课我们将手写两个关键模块并将其装载入 Kernel：

1. **LLM 多供应商适配器**：支持 DeepSeek、OpenAI、Anthropic Claude 等，纯手写 httpx SSE 流式解析 + tool_calls 增量拼接；
2. **核心工具集**：文件系统、代码搜索、AST 分析、精确编辑、受限 Shell、Git 自动化、代码审查。

#### 1. LLM 多供应商架构设计

在工业级 Code Agent 中，**不能只绑定一个 LLM 供应商**。用户可能更换底座模型、迁移到其他 API 或使用私有部署。我们设计一个统一的适配器接口：

```
                     +-------------------+
                     |   LLMRegistry     |
                     |   (供应商注册表)   |
                     +--------+----------+
                              |
            +-----------------+-----------------+
            |                 |                 |
   +--------+--------+  +----+----+  +--------+--------+
   | OpenAI Compat   |  |Anthropic|  | 自定义 (扩展点)  |
   | (DeepSeek/OpenAI|  | Claude  |  |                  |
   |  Kimi/通义千问)  |  |         |  |                  |
   +-----------------+  +---------+  +------------------+
```

所有适配器实现同一套接口：`chat_stream(messages, tools) -> (content, tool_calls, usage)`，流式输出通过 `TypedEventBus` 的 `"llm:stream"` 事件实时广播。`usage` 是模型返回的准确 Token 统计（含 `prompt_cache_hit_tokens`），供第 4 课讲到的命中率度量使用；供应商不支持时返回 `None`，由调用方回退估算。

#### 2. 手写 SSE 流式解析器（OpenAI 兼容协议）

OpenAI 兼容接口的 SSE 协议格式（DeepSeek、Kimi、通义千问、GLM 等均遵循）：

```text
data: {"choices":[{"delta":{"content":"Hello"}}]}
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file","arguments":"{\"path\":"}}]}]}
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":" \"src/main.ts\"}"}}]}]}
data: [DONE]
```

我们手写解析器，按 `index` 将 tool_calls 碎片增量拼接，并顺手从流式事件中提取 **usage**（模型返回的准确 Token 统计，第 4 课缓存命中率度量的数据源）：

```python
# litecode/llm/openai_compat.py（核心）
import json, httpx
from typing import Dict, Optional, Tuple

async def _parse_sse(self, response, events):
    full_content = ""
    tool_calls_map: Dict[int, ToolCall] = {}
    usage = None                          # 模型返回的准确 token 统计（末帧）
    buffer = ""

    async for chunk in response.aiter_bytes():
        buffer += chunk.decode("utf-8", errors="replace")
        lines = buffer.split("\n")
        buffer = lines.pop()

        for line in lines:
            line = line.strip()
            if not line or line.startswith(":"): continue
            if line == "data: [DONE]":
                return full_content, self._finalize(tool_calls_map), usage
            if not line.startswith("data: "): continue

            parsed = json.loads(line[6:])

            # usage 末帧：choices 为空、只有 usage 字段
            extracted = self._extract_usage(parsed)
            if extracted is not None:
                usage = extracted

            delta = (parsed.get("choices") or [{}])[0].get("delta")
            if not delta: continue

            # 增量文本
            if delta.get("content"):
                full_content += delta["content"]
                if events:
                    await events.emit("llm:stream", {"chunk": delta["content"]})

            # 增量 Tool Calls 拼接
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                target = tool_calls_map.get(idx)
                if target is None:
                    target = ToolCall(id=tc.get("id",""), name="", arguments="")
                    tool_calls_map[idx] = target
                if tc.get("id"): target.id = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"): target.name += fn["name"]
                if fn.get("arguments"): target.arguments += fn["arguments"]

    return full_content, [c for c in tool_calls_map.values() if c.name], usage
```

**关键要点**（与第1课一致）：
- 必须按 `index` 逐片累加 `arguments`，不能在中途 `json.loads`；
- 流式接收过程中 `arguments` 是非法 JSON 串，必须在流结束后才解析；
- `tool_calls_map` 的 key 是 `index`，保证并发多个 tool_call 也能正确拼接；
- **usage 只在流末尾返回一次**（`choices` 为空、仅剩 `usage` 字段），因此请求时必须带上 `stream_options: {"include_usage": True}` 才能收到这一帧。

usage 提取的辅助方法（统一字段格式，供第 4 课的命中率统计直接消费）：

```python
# litecode/llm/openai_compat.py（核心）
def _extract_usage(self, parsed):
    """从流式 chunk 提取 usage（末帧返回，choices 为空）。"""
    usage = parsed.get("usage")
    if not usage or not isinstance(usage, dict): return None
    prompt = usage.get("prompt_tokens")
    if not isinstance(prompt, int): return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": usage.get("completion_tokens", 0),
        "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
    }
```

#### 3. Anthropic Claude 适配器

Anthropic 的 API 格式与 OpenAI 兼容不同，主要体现在：
- 鉴权头：`x-api-key` 而非 `Authorization: Bearer`
- 消息格式：`system` 是独立字段，`tool_result` 嵌入在 `user` 消息的 `content` 数组中
- 事件格式：`content_block_start` / `content_block_delta(text_delta)` / `content_block_delta(input_json_delta)` / `content_block_stop`

核心适配器代码（`litecode/llm/anthropic.py`）：

```python
class AnthropicAdapter(BaseLLMAdapter):
    async def _parse_sse(self, response, events):
        full_content = ""
        tool_calls = []
        current_tool = None
        usage = None

        async for chunk in response.aiter_bytes():
            # 解析 event: message_start / content_block_start / content_block_delta /
            #             content_block_stop / message_delta
            # text_delta → 累积文本；input_json_delta → 累积 tool arguments
            ...
            if ev_type == "message_start":
                # input_tokens + cache_read_input_tokens（缓存命中部分）
                usage = self._start_usage(parsed)
            elif ev_type == "message_delta":
                delta = self._delta_usage(parsed)
                if delta is not None:
                    # message_delta.usage 是请求累计值（非增量），直接覆盖
                    usage["completion_tokens"] = delta["output_tokens"]
                    usage["prompt_cache_hit_tokens"] = delta["cache_read_input_tokens"]

        return full_content, [tc for tc in tool_calls if tc.name], usage
```

Anthropic 的 usage 分布在两类事件里，且**口径与 OpenAI 兼容接口不同**，这是第 4 课命中率统计必须特判的地方：

```python
def _start_usage(self, parsed):
    """message_start 事件：input_tokens 不含 cache_read。"""
    m_usage = (parsed.get("message") or {}).get("usage") or {}
    if not isinstance(m_usage.get("input_tokens"), int): return None
    return {
        "prompt_tokens": m_usage["input_tokens"],
        "completion_tokens": m_usage.get("output_tokens", 0),
        "prompt_cache_hit_tokens": m_usage.get("cache_read_input_tokens", 0),
    }

def _delta_usage(self, parsed):
    """message_delta 事件：output/cache_read 为累计值（非增量）。"""
    d_usage = parsed.get("usage") or {}
    if not isinstance(d_usage.get("output_tokens"), int): return None
    return {
        "output_tokens": d_usage.get("output_tokens", 0),
        "cache_read_input_tokens": d_usage.get("cache_read_input_tokens", 0),
    }
```

**口径差异**：OpenAI 兼容接口的 `prompt_tokens` 已包含缓存命中部分（`prompt_cache_hit_tokens`）；而 Anthropic 的 `input_tokens` **不含** `cache_read_input_tokens`——真实输入 = `input_tokens + cache_read`。因此计算 miss 时两家不能共用同一公式（详见第 4 课 §7）。

#### 4. LLM 供应商注册表 (`litecode/llm/registry.py`)

注册表管理多供应商配置、构建适配器、测试连接：

```python
class LLMRegistry:
    def __init__(self, llm_config=None):
        # 预置 7 个供应商：deepseek / openai / kimi / qwen / glm / anthropic / custom
        self.providers: Dict[str, Dict] = {pid: {...} for pid in PROVIDER_META}
        self.active = "deepseek"
        self._adapter = None
        self._apply_env_defaults()   # 从环境变量注入 API Key
        if llm_config:
            self.apply_config(llm_config)

    def build_adapter(self, provider_id=None, overrides=None):
        # 根据 provider_id 对应的 kind 选择 OpenAICompatAdapter 或 AnthropicAdapter
        ...

    def get_adapter(self) -> BaseLLMAdapter: ...
    def test_connection(self, provider_id, overrides) -> Tuple[bool, str, float]: ...
```

配置存储在 `.lite-code/config.json` 的 `"llm"` 段中：

```json
{
  "llm": {
    "active": "deepseek",
    "providers": {
      "deepseek": {"api_key": "sk-...", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash", "temperature": 0.2},
      "openai": {"api_key": "", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "temperature": 0.2}
    }
  }
}
```

#### 5. 模型元数据服务 (`litecode/llm/model_meta.py`)

上下文窗口长度（`context_window`）直接决定第 17 课的压缩阈值（`min(预算, 90% × 窗口)`）与「上下文情况」面板的占用率计算——而它随模型而异（DeepSeek V4 是 1M，Kimi K2 是 262K，GLM-4-Long 也是 1M）。各厂商的 `/models` 接口并不返回上下文长度，业界标准做法（OpenCode 同款）是使用社区模型元数据库 **models.dev**：

```python
# litecode/llm/model_meta.py（核心）
MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600     # 磁盘缓存 7 天

class ModelMetaService:
    def refresh(self) -> bool:
        # 拉取 models.dev 全量数据 → 拍平为 model_id → entry 索引 → 落盘缓存
        # 网络失败静默返回 False（离线可用）

    def get_context_window(self, model_id) -> Optional[int]:
        # 从磁盘缓存按模型 ID 精确匹配，命中 limit.context 返回
```

注册表对外提供 **四级解析**（`llm/registry.py`），优先级从高到低：

1. **手动覆盖**：用户在设置面板手填的 `context_window`（第 19 课配置界面）；
2. **models.dev 缓存**：按模型 ID 精确匹配的远程元数据；
3. **内置静态表**：`PROVIDER_META` 中维护的 per-model 常备数据；
4. **默认兜底**：128K。

```python
def get_context_window(self, provider_id, model=None) -> int:
    # ① 手动覆盖 → ② models.dev → ③ 内置表 → ④ 128K 默认
```

同步时机放在 FastAPI 的启动生命周期里（见第 19 课后端装配），异步执行、失败静默降级：

```python
# server/app.py — FastAPI lifespan
@asynccontextmanager
async def _lifespan(_app):
    await asyncio.to_thread(app.refresh_model_meta)   # 失败静默降级到内置表
    yield
```

#### 6. 工具注册表与核心工具集

工具全部通过 `ToolRegistry` 统一注册，每个工具有 `name`、`description`、`parameters`（JSON Schema）和执行函数：

```python
# litecode/tools/registry.py
class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._handlers: Dict[str, Handler] = {}

    def register(self, name, description, parameters, handler):
        self._tools[name] = ToolDefinition(...)
        self._handlers[name] = handler

    async def execute(self, name, args) -> str:
        handler = self._handlers.get(name)
        if not handler: return f'[Error]: 未注册的工具 "{name}"'
        try:
            return await handler(args)
        except Exception as exc:
            return f"[Execution Exception]: {exc}"
```

**文件系统工具**（`tools/filesystem.py`）：增强防目录穿越 + gitignore 感知文件树：

```python
class FileSystemTools:
    def resolve(self, rel_path: str) -> str:
        target = os.path.abspath(os.path.join(self.workspace, rel_path))
        # 强制限定在 workspace 内
        if not target.startswith(self.workspace + os.sep):
            raise PermissionError(f"[Security]: 路径穿越: {rel_path}")
        return target

    # 工具：read_file / write_file / list_dir / file_tree
    # file_tree 使用 pathspec 库解析 .gitignore，自动过滤 node_modules/.venv/dist 等
```

**代码感知工具**（`tools/codebase.py`）：Ripgrep 高速搜索（`rg` 命令异步调用，带超时）：

```python
class CodebaseTools:
    async def _search(self, args):
        rg = shutil.which("rg")
        proc = await asyncio.create_subprocess_exec(
            rg, "--line-number", "--column", "--smart-case",
            "--max-count", str(max_results), query, ...)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return stdout.decode("utf-8", errors="replace")
```

**AST 语义工具**（`tools/ast_tools.py`）：基于 tree-sitter 提取符号大纲与骨架压缩：

```python
class ASTAnalyzer:
    def extract_outline(self, code: str, ext: str) -> List[SymbolOutline]:
        lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(code.encode("utf-8"))
        # 遍历 AST，提取 function_declaration / class_declaration / interface_declaration
        ...

    def generate_skeleton(self, code, ext, target_symbol):
        """骨架抽取：只保留目标函数的完整函数体，其余函数体替换为 {/* ... */}"""
        ...
```

**精确编辑工具**（`tools/editor.py`）：Search-and-Replace 模糊退避 + Unified Diff 锚点偏移：

```python
class BlockReplacer:
    def replace_block(self, source, search, replace):
        # 1. 精确匹配
        if search in source: return source.replace(search, replace, 1)
        # 2. 模糊匹配（逐行 trim 后匹配，保持原缩进）
        ...
```

**受限 Shell 工具**（`tools/shell.py`）：asyncio 子进程 + 超时 + 敏感环境变量擦除：

```python
class ShellTools:
    async def execute(self, name, args):
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in SENSITIVE_ENV_VARS}
        proc = await asyncio.create_subprocess_shell(
            command, cwd=self.workspace, env=clean_env, ...)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
```

**Git 工具**（`tools/git.py`）：五个只读/写操作（`git_status`/`diff`/`log`/`commit`/`branch`），破坏性操作（`git push --force`）由安全层拦截走审批。

**代码审查工具**（`tools/review.py`）：收集 git diff → 静态体检（AST 语法错误、反模式检测、安全隐患）→ 输出结构化报告：

```python
class ReviewTools:
    async def execute(self, name, args):
        changed_files = await self._get_changed_files(scope)
        for rel_path in changed_files:
            findings = self._review_file(full_path)
            # AST 错误计数 / 反模式扫描 / 文件大小警告
            ...
```

#### 7. 装配层 (`litecode/app.py`)

`AgentApp` 负责把内核、LLM 注册表、工具集、安全组件装配在一起：

```python
class AgentApp:
    def __init__(self, workspace, ...):
        self.llm_registry = LLMRegistry()
        self.session_store = SessionStore(...)
        self.guard = SecurityGuard()
        self.approval_gate = ApprovalGate()
        self.sub_agent_runner = SubAgentRunner(self)  # 第13课，延迟绑定

    def build_registry(self, allowed=None, exclude=None) -> ToolRegistry:
        # 注册全部 17 个工具，支持按角色裁剪
        fs_tools = FileSystemTools(self.workspace)
        codebase = CodebaseTools(self.workspace)
        ...
```

#### 本课小结

在本课中，我们完成了 `lite-code` 的关键 LLM 适配层与工具集：

1. 实现了**纯手写 httpx SSE 流式解析器**，支持 OpenAI 兼容接口与 Anthropic 两种协议，并从流式事件中提取真实 usage（`prompt_cache_hit_tokens`），供第 4 课的缓存命中率度量使用；
2. 设计了**多供应商注册表**，预置 7 个供应商，支持环境变量兜底、配置热加载、测试连接；
3. 接入 **models.dev 模型元数据服务**：启动同步 + 7 天磁盘缓存 + 内置表兜底，`get_context_window` 四级解析上下文窗口；
4. 编写了**17 个核心工具**，覆盖文件读写、代码搜索、AST 分析、精确编辑、Shell 执行、Git 操作、代码审查；
5. 通过 `AgentApp` 装配层将所有模块组合在一起。

下一次我们将开启 **第17课：AgentLoop 主循环 (`lite-code` 实战第三篇)** —— 实现驱动整个 ReAct 循环的核心状态机，并把第 2-5 课的所有增强机制（JSON 自愈、死循环检测、Token 预算、静态 System Prompt、缓存感知截断）全部集成！
在上一课中，我们构建了 `lite-code` 的核心内核（`Kernel`、`TypedEventBus`、`Pipeline` 和 `SessionStore`）。

本课我们将手写两个关键模块并将其装载入 Kernel：

1. **LLM 多供应商适配器**：支持 DeepSeek、OpenAI、Anthropic Claude 等，纯手写 httpx SSE 流式解析 + tool_calls 增量拼接；
2. **核心工具集**：文件系统、代码搜索、AST 分析、精确编辑、受限 Shell、Git 自动化、代码审查、Web 抓取与 Skills 技能加载。

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
    details = (usage.get("prompt_tokens_details")
               or usage.get("input_tokens_details") or {})
    hit = (usage.get("prompt_cache_hit_tokens")
           or usage.get("cache_hit_tokens")
           or usage.get("cache_read_tokens")
           or usage.get("cached_tokens")
           or details.get("cached_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": usage.get("completion_tokens", 0),
        "prompt_cache_hit_tokens": hit if isinstance(hit, int) else 0,
    }
```

不同供应商的 usage 字段不能直接暴露给上层。OpenAI 兼容服务可能使用 `cached_tokens`、`cache_read_tokens` 或嵌套的 `prompt_tokens_details.cached_tokens`；Anthropic 则通过 `cache_read_input_tokens` 返回命中输入。适配器负责把这些字段归一化为 `prompt_cache_hit_tokens`，AgentLoop 只处理统一结构。如果响应没有缓存字段，应将其视为“缓存数据不可观测”，而不是强行推断为未命中。

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
        # 预置供应商 + 任意数量的 custom_* OpenAI 兼容实例
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

自定义服务不应共用一个固定的 `custom` 槽位。每个 `custom_*` 实例独立保存显示名称、Base URL、API Key、当前模型和模型列表，注册表根据当前 `active` ID 构建对应适配器。这样同一类 OpenAI 兼容协议可以同时连接多个不同网关，而不会互相覆盖配置。

多实例之外还有一类真实需求：**有些服务对请求头有额外要求**。典型如 OpenRouter 要求附带 `HTTP-Referer` / `X-Title` 用于应用归因统计；企业内网网关则常用自定义鉴权头（如 `X-Api-Token`）替代标准的 `Authorization: Bearer`。为此每个供应商配置支持 `custom_headers` 字段（任意多个键值对），适配器在构造时接收并按 HTTP 规范合并：自定义头以大小写不敏感方式覆盖默认头：

```python
from .base import merge_headers

# 默认头 + 自定义头合并；HTTP Header 名称大小写不敏感
# authorization 与 Authorization 视为同一个头，最终只保留自定义值
def _headers(self) -> Dict[str, str]:
    return merge_headers(
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        },
        self.custom_headers,
    )
```

`custom_headers` 经 `clean_custom_headers` 清洗（仅保留 `str: str`、去空键空值）后才进入适配器——配置层校验失守时这是最终防线。注册表的 `build_adapter` 与 `to_config` 均透传该字段，因此 `chat_stream` 与「测试连接」共用 `_headers()`，设置界面填完头点测试即可验证真实生效。设置界面用多行文本编辑（每行 `Key: Value` 或 `Key=Value`，按第一个分隔符切分，值里可以再含冒号——URL 就常见）。保存和测试连接都会直接读取当前文本框内容，不依赖失焦；清空文本并保存会明确清除旧 Header。

**Custom Headers 支持会话模板变量**：值中可以嵌入 `{session_id}`、`{conversation_id}`、`{workspace}`、`{model}`、`{provider}` 五个占位符，发送请求前由适配器替换为当前任务的对应值（`expand_header_templates`）。典型场景是网关级服务要求"每个会话一个稳定标识"以配合 prompt 缓存——例如 OpenCode Go 要求 `x-opencode-session: <会话 ID>`，只需在 Custom Headers 里写 `x-opencode-session: {conversation_id}` 即可：

```python
# llm/base.py（核心）：值中 {var} 在发送前替换，无会话上下文时该头自动丢弃
HEADER_TEMPLATE_KEYS = ("session_id", "conversation_id", "workspace", "model", "provider")

def expand_header_templates(headers, context=None):
    ctx = context if context is not None else dict(header_context.get())
    for key, value in clean_custom_headers(headers).items():
        for name in HEADER_TEMPLATE_KEYS:
            value = value.replace("{" + name + "}", ctx.get(name, ""))
        # 模板展开后为空（如测试连接无会话）→ 丢弃，避免把空头发给服务端
```

其中 `{conversation_id}` 由 `SessionStore.get_or_create_conversation_id` 按 **(会话 × 供应商)** 组合惰性生成 UUID 并写入会话 metadata——同一会话对同一供应商跨重启稳定复用，不同供应商各持一个独立 ID，避免第三方通过同一标识关联用户在不同网关的行为。模板展开读取 `header_context` ContextVar，由 `AgentLoop.run_task` 在每个任务开始时填充（与 todo 工具的 `current_session_id` 同一机制），因此全局复用的适配器单例也能在每个请求上拿到正确的会话值。

#### 4.5 推理强度控制（reasoning_effort）

推理模型（OpenAI o 系列、DeepSeek R1、Anthropic 扩展思考、Kimi K2、GLM-4.6 等）允许用户控制"思考深度"：回答前花多少 Token 推理。`lite-code` 把它抽象为统一的 `reasoning_effort` 配置（关闭/低/中/高/最大），不同供应商以不同方式落地。

**OpenAI 兼容接口**：直接透传 `reasoning_effort` 字段；**启用时省略 `temperature`**——推理模型通常不接受 temperature，同时传会报 400：

```python
# llm/openai_compat.py（核心）
def _build_payload(self, messages, tools):
    payload = {
        "model": self.model,
        "messages": [m.to_dict() for m in messages],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if self.reasoning_effort:
        # 推理模型：透传 reasoning_effort，省略 temperature
        payload["reasoning_effort"] = self.reasoning_effort
    else:
        payload["temperature"] = self.temperature
    ...
```

**Anthropic**：没有 `reasoning_effort` 字段，用扩展思考（extended thinking）实现，映射为 `thinking.budget_tokens`：

```python
# llm/anthropic.py（核心）
_THINKING_BUDGETS = {"low": 2048, "medium": 8192, "high": 32000, "max": 64000}

def _build_payload(self, messages, tools, system, enable_cache=True):
    payload = {"model": self.model, "max_tokens": self.max_tokens, "stream": True, ...}
    if self.reasoning_effort:
        budget = self._THINKING_BUDGETS.get(self.reasoning_effort, 8192)
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        # max_tokens 必须大于 budget_tokens，否则自动抬高
        if payload["max_tokens"] <= budget:
            payload["max_tokens"] = budget + 4096
    else:
        payload["temperature"] = self.temperature
    ...
```

**流式思考内容**：推理模型的思考过程也会以流式增量返回（OpenAI 兼容接口叫 `reasoning_content`/`thinking`，Anthropic 叫 `thinking_delta`），适配器把它们与正文一起转发给 UI——用户能看到"模型在思考什么"，而不是干等首 Token：

```python
# openai_compat.py：推理增量
stream_content = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking")
# anthropic.py：thinking_delta
elif delta.get("type") == "thinking_delta":
    full_content += delta.get("thinking", "")
```

**能力探测**：不是所有模型都支持推理控制。`PROVIDER_META` 为每个供应商维护 `reasoning_models` 列表，`provider_meta()` 返回 `reasoning_supported`（当前模型是否在列表中）+ `reasoning_models`（完整列表），前端据此提示用户"当前模型可能不支持推理"。自定义模型无法枚举，用户仍可手动开启尝试——失败时后端会返回明确的 400 错误。

#### 5. 模型元数据服务 (`litecode/llm/model_meta.py`)

上下文窗口长度（`context_window`）是后续实战两处关键计算的输入——上下文压缩阈值与「上下文情况」面板的窗口占用率。它随模型而异（DeepSeek V4 是 1M，Kimi K2 是 262K，GLM-4-Long 也是 1M）。各厂商的 `/models` 接口并不返回上下文长度，业界标准做法（OpenCode 同款）是使用社区模型元数据库 **models.dev**：

```python
# litecode/llm/model_meta.py（核心）
MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600     # 磁盘缓存 7 天

class ModelMetaService:
    def refresh(self) -> bool:
        # TTL 挡板：磁盘缓存不足 7 天 → 直接加载缓存返回，不发网络请求
        # 过期/缺失才拉取 models.dev → 拍平为 model_id → entry 索引 → 落盘
        # 网络失败静默返回 False（离线可用）

    def get_context_window(self, model_id) -> Optional[int]:
        # 从磁盘缓存按模型 ID 精确匹配，命中 limit.context 返回
```

注册表对外提供 **四级解析**（`llm/registry.py`），优先级从高到低：

1. **手动覆盖**：用户在设置面板手填的 `context_window`（第 21 课会实现这个输入框）；
2. **models.dev 缓存**：按模型 ID 精确匹配的远程元数据；
3. **内置静态表**：`PROVIDER_META` 中维护的 per-model 常备数据；
4. **默认兜底**：128K。

```python
def get_context_window(self, provider_id, model=None) -> int:
    # ① 手动覆盖 → ② models.dev → ③ 内置表 → ④ 128K 默认
```

**models.dev 数据不止上下文窗口**：每个模型的条目还带 `cost: {input, output, cache_read}`（每百万 token 美元）。`get_model_pricing` 按当前任务的模型取这三价，交给第 4 课 §5 的分段计价——**成本估算的价格必须 per-model**，静态全局价对任何具体模型都是错的（DeepSeek flash 输入 $0.14/M 与 GPT-4o $2.5/M 差 18 倍，缓存折扣也各家不同）。模型 ID 匹配用三级回退：全名（`deepseek/deepseek-v4-flash`）→ `provider/model` 拼接 → 裸模型名；无数据（自定义实例）返回 None，调用方回退 config 静态价。

同步时机放在 FastAPI 的启动生命周期里（第 21 课装配后端时接入）——**后台异步、绝不阻塞启动**：

```python
# server/app.py — FastAPI lifespan
@asynccontextmanager
async def _lifespan(_app):
    # 启动零网络等待：刷新丢进后台线程，查询侧走缓存/内置表
    _refresh_task = asyncio.create_task(asyncio.to_thread(app.refresh_model_meta))
    yield
    _refresh_task.cancel()   # 应用关闭时回收
```

**为什么启动不能等网络**：早期版本在 lifespan 里 `await to_thread(refresh)`——每次启动都同步拉取 models.dev（4.4MB），网络慢时桌面应用启动被拖住十几秒，测试也随机超时。修复确立两条铁律：**① TTL 挡板放在刷新入口**——缓存不足 7 天时 `refresh()` 只是读盘，网络零开销（TTL 只放读路径、刷新路径不检查，等于没有 TTL）；**② 刷新永远在后台**——`create_task` 丢后台就返回，刷新结果落盘后自然生效，用户操作零感知。查询路径（`get_context_window`）则完全不触发网络，永远从缓存/内置表读。

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

**AST 语义工具**（`tools/ast_tools.py`）：基于 Tree-sitter 提取 TypeScript、JavaScript、Java、Go 符号大纲与骨架；Python 使用标准库 `ast`：

```python
class ASTAnalyzer:
    def extract_outline(self, code: str, ext: str) -> List[SymbolOutline]:
        # tree-sitter-typescript 提供两种语法：language_typescript（纯 TS/JS）
        # 与 language_tsx（含 JSX 标签），按扩展名选择对应语法
        lang = (tree_sitter.Language(tree_sitter_typescript.language_tsx())
                if ext in (".tsx", ".jsx")
                else tree_sitter.Language(tree_sitter_typescript.language_typescript()))
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(code.encode("utf-8"))
        # 遍历 AST，提取 function_declaration / class_declaration / interface_declaration
        ...

    def generate_skeleton(self, code, ext, target_symbol):
        """骨架抽取：只保留目标函数的完整函数体，其余函数体替换为 {/* ... */}"""
        ...
```

**精确编辑工具**（`tools/editor.py`）：Search-and-Replace 模糊退避 + Unified Diff 锚点偏移。成功回执使用 `difflib.unified_diff` 生成 `[Patch Success]: 已更新 <path> (+N -M)` 增删统计并附上 diff 正文（超 4000 字符截断）——既让 Agent 自检刚做的修改，也为第 21 课 Web UI 的"文件修改卡片"准备好渲染数据：

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

**Web 抓取工具**（`tools/web.py`）：对标 OpenCode 的 `webfetch`，解决 Agent 缺少联网能力时凭记忆/臆测回答外部信息的问题：

```python
class WebFetchTools:
    # webfetch        : 抓取单个 URL → HTML 转 Markdown（纯标准库转换，不依赖 bs4）
    # webfetch_batch  : 批量抓取最多 8 个 URL（信号量并发 4），单页失败不影响其余
    def validate_url(self, url):
        # 1. 协议白名单：仅 http/https（拦 file:// 等本地协议）
        # 2. SSRF 防护：解析主机名后拒绝回环/私网/链路本地/保留地址
        ...
    # 磁盘缓存：.lite-code/webfetch_cache/ 按 URL SHA256 存 JSON，默认 TTL 1 小时
    # 输出上限：2MB 读取上限 + maxChars 截断，防止爆上下文
```

**Skills 工具**（`tools/skills.py`）：即第 8 课设计的 `load_skill`——发现项目级/用户级 `skills/*/SKILL.md`，索引注入 System Prompt，全文按需加载。这里只需把它封装成 `SkillsPlugin` 挂进插件列表（`SkillsPlugin(self.workspace)`），无需任何额外接线。

#### 7. 装配层 (`litecode/app.py`)

`AgentApp` 负责把内核、LLM 注册表、工具集、安全组件装配在一起。工具集采用第 10 课的 **Cordis 插件模式**：内核只持有 `tools` 服务（`ToolRegistry`）与裁剪策略服务（`tool_filter`），具体工具全部由 `tools/plugin.py` 的 `ToolPlugin` 插件在 install 时注册：

```python
# litecode/tools/plugin.py（第 10 课「空间解耦」落地）
class ToolPlugin(Plugin):
    def install(self, kernel):
        registry = kernel.get_service("tools")       # 依赖注入：取内核服务
        allow = kernel.get_service("tool_filter")    # Agent 裁剪策略
        for tool in self.get_tools():
            if allow is not None and not allow(tool.name):
                continue
            registry.register(tool.name, tool.description, tool.parameters,
                              lambda args, n=tool.name: self.execute(n, args))

class AgentApp:
    def __init__(self, workspace, ...):
        self.llm_registry = LLMRegistry()
        self.session_store = SessionStore(...)
        self.guard = SecurityGuard()
        self.approval_gate = ApprovalGate()
        self.sub_agent_runner = SubAgentRunner(self)  # 第11课，延迟绑定

    def tool_plugins(self):   # 10 个工具插件：文件/搜索/AST/编辑/Shell/Skills/Git/审查/Web/子 Agent
        return [FileSystemPlugin(self.workspace), CodebasePlugin(self.workspace),
                ASTPlugin(self.workspace), EditorPlugin(self.workspace),
                ShellPlugin(self.workspace), SkillsPlugin(self.workspace),
                GitPlugin(self.workspace), ReviewPlugin(self.workspace),
                WebFetchPlugin(cache_dir=...), SubAgentPlugin(self)]

    def build_registry(self, allowed=None, exclude=None, permissions=None) -> ToolRegistry:
        # 引导内核组装：tools 服务 + tool_filter 服务 + 全部工具插件
        kernel = Kernel(session_id="tool-bootstrap")
        registry = ToolRegistry()
        kernel.register_service("tools", registry)
        kernel.register_service("tool_filter", self._tool_filter(allowed, exclude, permissions))
        for plugin in self.tool_plugins():
            kernel.use(plugin)
        return registry
```

#### 本课小结

在本课中，我们完成了 `lite-code` 的关键 LLM 适配层与工具集：

1. 实现了**纯手写 httpx SSE 流式解析器**，支持 OpenAI 兼容接口与 Anthropic 两种协议，并从流式事件中提取真实 usage（`prompt_cache_hit_tokens`），供第 4 课的缓存命中率度量使用；
2. 设计了**多供应商注册表**，预置供应商并支持任意数量的 `custom_*` OpenAI 兼容实例，提供环境变量兜底、配置热加载、测试连接；
3. 接入 **models.dev 模型元数据服务**：启动同步 + 7 天磁盘缓存 + 内置表兜底，`get_context_window` 四级解析上下文窗口、`get_model_pricing` 取 per-model 定价（含缓存命中价）；
4. 编写了**20 个内置工具**，覆盖文件读写、代码搜索、AST 分析、精确编辑、Shell 执行、Git 操作、代码审查、Web 抓取和技能加载；MCP 工具由配置动态增加；
5. 通过 `AgentApp` 装配层将所有模块组合在一起。

下一次我们将开启 **第16课：AgentLoop 主循环与增强集成（lite-code 实战第三篇）** —— 实现驱动整个 ReAct 循环的核心状态机，并把第 2-5 课的所有增强机制（JSON 自愈、死循环检测、Token 预算、静态 System Prompt、缓存感知截断）全部集成！

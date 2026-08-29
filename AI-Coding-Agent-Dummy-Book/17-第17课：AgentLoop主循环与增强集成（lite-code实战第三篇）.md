在前两课中，我们分别完成了 `lite-code` 的内核基础设施（Core）与 LLM 多供应商适配器 + 核心工具集。

本课我们将手写驱动整个框架的**核心引擎——AgentLoop 主循环状态机**。它负责协调 LLM 交互、工具安全校验、自动派发执行、结果回填以及会话落盘，完成完整的 "Think → Act → Observe" 闭环，并集成第 2-3 课的所有增强机制。

#### 1. AgentLoop 架构设计

AgentLoop 是一个异步有限状态机（FSM），其标准运行流程如下：

```
                    +--------------------------+
                    |    User Input Message    |
                    +------------+-------------+
                                 |
                                 v
+-------------------------------------------------------------------+
|                         Agent Loop                                 |
|                                                                    |
|   +-------------------+  Stream Output   +----------------------+  |
|   | 1. Invoke LLM     | --------------> | Streaming (SSE/UI)   |  |
|   +--------+----------+                  +----------------------+  |
|            |                                                        |
|            | Has tool_calls?                                        |
|       +----+----+                                                   |
|       |         |                                                   |
|    No |         | Yes                                               |
|       v         v                                                   |
|  +--------+  +--------------------------------------------------+  |
|  |  Return |  | 2. for each tool_call:                           |  |
|  |  Result |  |    - 死循环检测 (第2课)                           |  |
|  +--------+  |    - JSON 容错解析 (第2课)                         |  |
|              |    - beforeTool 安全管道 (第15课) → 审批            |  |
|              |    - 执行工具 (超时)                               |  |
|              |    - 输出截断 (第2课)                              |  |
|              +-----------------------+---------------------------+  |
|                                      |                             |
|                                      v                             |
|              +--------------------------------------------------+  |
|              | 3. 结果回填 → 会话落盘 → 回到步骤 1               |  |
|              +--------------------------------------------------+  |
+-------------------------------------------------------------------+
```

#### 2. 第 2 课增强机制集成

在进入 AgentLoop 主代码之前，先看三个第 2 课引入的防御模块：

**JSON 容错（`core/json_repair.py`）**：解析失败绝不 crash，回填错误信息给 LLM 自愈：

```python
def safe_json_parse(json_string: str):
    try:
        return True, json.loads(json_string), ""
    except Exception as e:
        cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", json_string).strip()
        try:
            return True, json.loads(cleaned), ""
        except Exception:
            return False, None, f"JSON Parse Failed: {e}. 请将参数格式化为合法 JSON。"
```

**死循环检测（`core/state_tracker.py`）**：连续 N 次相同参数调用同一工具 → 强行中断：

```python
class AgentStateTracker:
    def register_and_check_loop(self, tool_name, args_str):
        action_hash = f"{tool_name}:{args_str.strip()}"
        self.history_action_hashes.append(action_hash)
        last = self.history_action_hashes[-self.loop_threshold:]
        if len(set(last)) == 1:
            return True  # 触发死循环预警
        return False
```

**输出截断（`core/truncator.py`）**：行+字节双上限，保留头部，超限时落盘（第4/5课增强）：

```python
def truncate_tool_output(output, max_lines=2000, max_bytes=51200,
                         direction="head", output_dir=None):
    if not output: return TruncationResult(output, False)
    lines = output.split("\n")
    if len(lines) <= max_lines and len(output.encode("utf-8")) <= max_bytes:
        return TruncationResult(output, False)
    # 保留头部，超限内容落盘到 output_dir，提示模型按需读取
    ...
```

#### 3. 第 3 课增强机制集成

**Token 预算估算（`core/token_counter.py`）**：中文加权启发式，1 Token ≈ 4 英文字符 / 0.75 中文字符：

```python
class TokenCounter:
    @staticmethod
    def count_text_tokens(text: str) -> int:
        cjk_count = len(re.findall(r"[\u4e00-\u9fa5]", text))
        non_cjk = len(text) - cjk_count
        return max(1, int(cjk_count * 1.3 + non_cjk / 3.8))
```

**上下文滑动窗口裁剪（`core/context_manager.py`）**：关键约束：Index 0 的 System Prompt 永远不能删；`assistant(tool_calls)` 与 `tool(result)` 必须作为原子对存在或一起被裁剪：

```python
class ContextManager:
    def prune_messages(self, messages) -> List[Message]:
        # 从最早的历史消息开始丢弃，但跳过 system 消息
        # 如果遇到 assistant 带 tool_calls，必须同时删除后续对应的 tool 消息
        ...
```

**动态 System Prompt（`core/system_prompt.py`）**：每次调用 LLM 前实时注入 OS、CWD、Git 分支、工具列表等环境信息：

```python
class SystemPromptBuilder:
    @classmethod
    def build(cls, cwd, tools):
        os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"
        tools_summary = "\n".join(f"- **{t.name}**: {t.description}" for t in tools)
        return f"你是一个专业的 AI 软件工程师 Code Agent...\n### 环境信息\n- 操作系统: {os_name}\n- 工作目录: `{cwd}`\n- Git: {cls._git_info(cwd)}\n### 可用工具\n{tools_summary}\n### 工作规则..."
```

#### 4. AgentLoop 主循环代码 (`litecode/core/agent_loop.py`)

将所有防御机制和工具分发集成在 FSM 中：

```python
class AgentLoop:
    def __init__(self, kernel, adapter, registry, session_store=None,
                 context_manager=None, max_steps=25, tool_timeout=120,
                 token_budget=48000, pricing=None):
        self.kernel = kernel
        self.adapter = adapter          # LLM 适配器（多供应商）
        self.registry = registry        # 工具注册表
        self.session_store = session_store
        self.context_manager = context_manager or ContextManager(token_budget)
        self.max_steps = max_steps
        self.tool_timeout = tool_timeout
        self.state = AgentStateTracker()
        self.abort_event = None         # 停止信号（asyncio.Event）

    async def run_task(self, prompt, system_prompt=None, tools=None, store_snapshot=True):
        messages = self.kernel.ctx.messages

        # 1. System Prompt 初始化
        if not messages or messages[0].role != "system":
            messages.insert(0, Message(role="system", content=system_prompt))

        # 2. 用户消息入链
        messages.append(Message(role="user", content=prompt))
        await self.kernel.events.emit("task:start", ...)

        for step in range(self.max_steps):
            if self._check_abort():
                return "[Stopped]: 已由用户手动停止。"

            # A. 上下文裁剪
            payload = self.context_manager.prune_messages(messages)

            # B. 动态 System Prompt（每次调用前实时注入）
            if payload[0].role == "system":
                payload[0] = Message(role="system",
                    content=SystemPromptBuilder.build(self.workspace, tools))

            # C. beforeLLM 管道（插件可修改消息）
            processed = await self.kernel.before_llm.run(self.kernel.ctx, payload)

            # D. 调用 LLM（流式，内部 emit llm:stream）
            content, tool_calls = await self.adapter.chat_stream(
                processed, tools, self.kernel.events)

            # E. Assistant 消息入链
            messages.append(Message(role="assistant",
                content=content or None, tool_calls=tool_calls or None))

            # F. 无工具调用 → 任务收敛
            if not tool_calls:
                self.state.status = AgentStatus.SUCCESS
                if store_snapshot: self._save_session()
                return content or "(空回复)"

            # G. 顺序派发执行工具
            for call in tool_calls:
                # 死循环检测
                if self.state.register_and_check_loop(call.name, call.arguments):
                    self._push_tool_error(call, f"死循环！已连续 {self.state.loop_threshold} 次相同参数调用 {call.name}。请换策略。")
                    continue

                # JSON 容错解析
                ok, args, error = safe_json_parse(call.arguments)
                if not ok:
                    self._push_tool_error(call, error)
                    continue

                # beforeTool 安全管道（SecurityPlugin 等）
                hook_data = {"toolName": call.name, "args": args, "cancel": False, "reason": ""}
                verified = await self.kernel.before_tool.run(self.kernel.ctx, hook_data)

                if verified.get("cancel"):
                    result = f"[Tool Execution Cancelled]: {verified.get('reason')}"
                else:
                    # 执行工具（带超时）
                    raw = await asyncio.wait_for(
                        self.registry.execute(call.name, args),
                        timeout=self.tool_timeout)
                    result = truncate_tool_output(raw)

                messages.append(Message(role="tool", name=call.name,
                    tool_call_id=call.id, content=result))

            # H. 每轮批量执行完成后落盘
            if store_snapshot: self._save_session()

        return "[Loop Terminated]: 超出最大步骤限制。"
```

#### 5. 子 Agent 编排 (`litecode/orchestration/sub_agent.py`)

对应第 11 课的子 Agent 编排，在 lite-code 中真实实现：创建独立 Kernel 和 AgentLoop，工具集按角色裁剪（explorer/read-only 不赋予写文件权限）：

```python
class SubAgentRunner:
    async def run_task(self, task_description, role="general", max_steps=12):
        sub_kernel = Kernel(session_id=f"sub_{uuid.uuid4().hex[:8]}")
        # 按角色裁剪工具集
        allowed = ROLE_TOOLS.get(role)
        registry = self.app.build_registry(allowed=allowed, exclude=["spawn_sub_agent"])
        loop = AgentLoop(kernel=sub_kernel, adapter=self.app.adapter,
                         registry=registry, session_store=None, ...)
        summary, stats = await loop.run_task(f"请完成：{task_description}", ...)
        return {"summary": summary, "total_tokens_used": ..., "completed": ...}
```

#### 本课小结

在第十四课中，我们实现了 `lite-code` 的灵魂模块——**AgentLoop 主循环**：

1. 掌握了完整的 **Think-Act-Observe 状态机** 控制逻辑；
2. 集成了第 2 课所有防御：**JSON 自愈**、**死循环 Hash 检测**、**输出截断**；
3. 集成了第 3 课所有增强：**Token 预算估算**、**滑动窗口裁剪**（保护 system 与 tool 原子对）、**动态 System Prompt**；
4. 实现了 **beforeTool 安全管道**（第 15 课 SecurityPlugin 的接入点）；
5. 主循环回收后自动**会话落盘**，防止中途异常崩溃丢状态；
6. 子 Agent 编排**真实化**：上下文隔离、只读工具裁剪、Token 归集。

下一次我们将开启 **第18课：安全沙箱与高危拦截实战 (`lite-code` 实战第四篇)** —— 给 `lite-code` 加入动态黑白名单、三级风险控制、Web 审批卡与提权确认机制！
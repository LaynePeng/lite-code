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
|              |    - beforeTool 安全管道 → 审批              |  |
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

**输出截断（`core/truncator.py`）**：行+字节双上限，保留头部，超限时落盘（第 2 课实现，第 5 课深化为带外存储）：

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

**工具调用原子对修复（`core/context_manager.py`）**：前三个防御都是"执行前拦截"，这一个是**发送前兜底**。OpenAI 兼容 API 硬性要求 `assistant(tool_calls)` 后必须为每个 `tool_call_id` 跟一条 tool 消息，缺一条就报 `HTTP 400`。但现实中链会破：任务被用户停止时落盘了不完整历史、供应商缺 id（第 1 课的坑）、压缩边界意外切断了原子对。主循环在**恢复历史后**和**每次调用 LLM 前**各跑一遍修复（`repair_tool_call_pairs`）：完整配对原样保留；残缺对连同孤儿 tool 消息一起丢弃；空 id 链按位置补齐一致的合成 id——宁可丢一轮旧工具细节，也不能把非法链发给 API：

```python
def repair_tool_call_pairs(messages) -> List[Message]:
    repaired = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.role == "assistant" and m.tool_calls:
            ids = {c.id for c in m.tool_calls if c.id}
            j = i + 1
            matched = []
            # 收集紧随的 tool 结果：正常链按 id 匹配，空 id 链按顺序匹配
            while j < len(messages) and messages[j].role == "tool":
                if ids and messages[j].tool_call_id not in ids:
                    break
                if not ids and messages[j].tool_call_id:
                    break
                matched.append(messages[j]); j += 1
            if len(matched) >= len(m.tool_calls):
                # 完整配对 → 保留；空 id 链按位置补齐一致的合成 id
                if not ids:
                    for c in m.tool_calls:
                        if not c.id:
                            c.id = f"call_{uuid.uuid4().hex[:12]}"
                    for k, t in enumerate(matched[: len(m.tool_calls)]):
                        if not t.tool_call_id:
                            t.tool_call_id = m.tool_calls[k].id
                repaired.append(m)
                repaired.extend(matched[: len(m.tool_calls)])
            # 残缺对 → 整对丢弃，绝不让非法链出站
            i = j
        elif m.role == "tool":
            i += 1                      # 无主 tool 消息 → 丢弃
        else:
            repaired.append(m); i += 1
    return repaired
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

**上下文滑动窗口裁剪（`core/context_manager.py`）**：关键约束：Index 0 的 System Prompt 永远不能删；`assistant(tool_calls)` 与 `tool(result)` 必须作为原子对存在或一起被裁剪。第 3 课已经完整推导了**策略 B 两阶段裁剪**的原理与实现，本课把它集成进主循环：先压缩更早轮次的工具细节（保留对话主干），还不够再按轮整轮删除（最新一轮永不删），并用标记数组把复杂度控制在 O(n)：

```python
class ContextManager:
    def __init__(self, max_allowed_tokens: int = 48000, keep_recent_full_turns: int = 2):
        self.max_allowed_tokens = max_allowed_tokens
        self.keep_recent_full_turns = max(1, keep_recent_full_turns)
        self.last_prune = {"compressed": False, "removed_tokens": 0, "stage": None}

    def prune_messages(self, messages, hard_cap=None) -> List[Message]:
        cap = hard_cap or self.max_allowed_tokens
        self.last_prune = {"compressed": False, "removed_tokens": 0, "stage": None}

        if TokenCounter.count_messages_tokens(messages) <= cap:
            return messages

        system, body = self._split_body(messages)
        sys_tokens = TokenCounter.count_message_tokens(system) if system else 0
        tokens = [sys_tokens] + [TokenCounter.count_message_tokens(m) for m in body]
        removed = [False] * (len(body) + 1)
        total = sum(tokens)

        # 阶段1：压缩更早轮次的工具细节（assistant(tool_calls)+tool 原子对）
        if total > cap:
            for ai, tool_idxs in self._stage1_candidates(body):
                if total <= cap:
                    break
                for idx in [ai + 1] + [t + 1 for t in tool_idxs]:
                    if not removed[idx]:
                        removed[idx] = True
                        total -= tokens[idx]
                self.last_prune["stage"] = "stage1"

        # 阶段2：整轮删除最老轮次（保留最新一轮）
        if total > cap:
            for start, end in self._oldest_turn_ranges(body):
                if total <= cap:
                    break
                for idx in range(start + 1, end + 1):
                    if not removed[idx]:
                        removed[idx] = True
                        total -= tokens[idx]
                self.last_prune["stage"] = "stage2"

        result = ([system] if system else []) + [
            m for m, r in zip(body, removed[1:]) if not r
        ]
        # 兜底：body 被删空时保留 system + 最新一轮
        if system is not None and len(result) == 1:
            newest = self._newest_turn(body)
            result = [system] + body[newest[0]:newest[1]]

        removed_tokens = (TokenCounter.count_messages_tokens(messages)
                          - TokenCounter.count_messages_tokens(result))
        self.last_prune.update(compressed=removed_tokens > 0,
                               removed_tokens=max(0, removed_tokens))
        return result

    @staticmethod
    def _split_body(messages):
        system = messages[0] if messages and messages[0].role == "system" else None
        return system, (messages[1:] if system else list(messages))

    @staticmethod
    def _turn_ranges(body) -> List[tuple]:
        """按 user 消息切成轮次，返回 [(start, end_excl), ...]。"""
        turns, start = [], None
        for i, m in enumerate(body):
            if m.role == "user":
                if start is not None:
                    turns.append((start, i))
                start = i
        if start is not None:
            turns.append((start, len(body)))
        if turns and turns[0][0] != 0:
            turns[0] = (0, turns[0][1])
        elif not body:
            turns = [(0, len(body))]
        return turns

    def _stage1_candidates(self, body) -> List[tuple]:
        """可删除的 assistant(tool_calls)+tool 对位置，最老在前；跳过最近 K 轮。"""
        turns = self._turn_ranges(body)
        keep_from = max(0, len(turns) - self.keep_recent_full_turns)
        candidates = []
        for turn_idx, (start, end) in enumerate(turns):
            if turn_idx >= keep_from:
                continue
            i = start
            while i < end:
                m = body[i]
                if m.role == "assistant" and m.tool_calls:
                    ids = {c.id for c in m.tool_calls}
                    tool_idxs = []
                    j = i + 1
                    while j < end and body[j].role == "tool" and body[j].tool_call_id in ids:
                        tool_idxs.append(j)
                        j += 1
                    candidates.append((i, tool_idxs))
                    i = j
                else:
                    i += 1
        return candidates

    def _oldest_turn_ranges(self, body) -> List[tuple]:
        """整轮删除顺序：最老优先，最新一轮永不删。"""
        turns = self._turn_ranges(body)
        return turns[:-1] if len(turns) > 1 else []

    def _newest_turn(self, body) -> tuple:
        turns = self._turn_ranges(body)
        return turns[-1] if turns else (0, len(body))
```

**静态 System Prompt（`core/system_prompt.py`）**：System 只含任务内恒定内容（角色、OS/CWD、工具摘要、规则），**任务开始时构建一次**，整轮任务内逐字节不变——这是第 4 课「稳定前缀」原则的真正落地。Git 状态等会随时间变化的信息**不进 System**，由 `git_status` 工具按需获取：

```python
class SystemPromptBuilder:
    @classmethod
    def build(cls, cwd, tools):
        os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"
        tools_summary = "\n".join(f"- **{t.name}**: {t.description}" for t in tools)
        return f"你是一个专业的 AI 软件工程师 Code Agent...\n### 环境信息\n- 操作系统: {os_name}\n- 当前工作目录: `{cwd}`\n### 可用工具\n{tools_summary}\n### 工作规则..."
```

> **与第 3 课的关系**：第 3 课展示了"动态 System Prompt"的概念价值（环境感知），并讨论了它与缓存红线的张力；实战落地的最终选择是**静态骨架 + 工具按需获取**——`git_status` 等工具返回的信息永远比 System 里预埋的快照新鲜，而前缀稳定性保住了缓存命中（详见第 3 课「设计决策」与第 4 课「稳定前缀」）。

#### 4. AgentLoop 主循环代码 (`litecode/core/agent_loop.py`)

将所有防御机制和工具分发集成在 FSM 中：

```python
class AgentLoop:
    def __init__(self, kernel, adapter, registry, session_store=None,
                 context_manager=None, max_steps=25, tool_timeout=120,
                 token_budget=48000, pricing=None, context_window=None):
        self.kernel = kernel
        self.adapter = adapter          # LLM 适配器（多供应商）
        self.registry = registry        # 工具注册表
        self.session_store = session_store
        self.context_manager = context_manager or ContextManager(token_budget)
        self.max_steps = max_steps
        self.tool_timeout = tool_timeout
        self.state = AgentStateTracker()
        self.abort_event = None         # 停止信号（asyncio.Event）
        self.context_window = context_window or 128_000   # 模型窗口（第3课：90% 安全边际）
        self.truncation_dir = None      # 截断落盘目录（第4/5课：超限输出保存到磁盘）
        self._compression_count = 0     # 上下文压缩次数（供「上下文情况」面板）
        self._compressed_tokens = 0     # 累计压缩节省的 token
        self._last_usage = None         # 最近一次 LLM 返回的 usage（第4课真实回填）

    async def run_task(self, prompt, system_prompt=None, tools=None, store_snapshot=True):
        messages = self.kernel.ctx.messages

        # 1. System Prompt 初始化（每任务一次：静态骨架，保证缓存前缀稳定）
        if system_prompt is None:
            system_prompt = SystemPromptBuilder.build(self.workspace, tools)
        if not messages or messages[0].role != "system":
            messages.insert(0, Message(role="system", content=system_prompt))
        else:
            messages[0].content = system_prompt

        # 2. 用户消息入链
        messages.append(Message(role="user", content=prompt))
        await self.kernel.events.emit("task:start", ...)

        stats = {
            "input_tokens": 0, "output_tokens": 0, "tool_calls": 0,
            "turns": 0, "blocked": 0,
            "cache_hit_tokens": 0, "cache_miss_tokens": 0,
        }

        for step in range(self.max_steps):
            if self._check_abort():
                return "[Stopped]: 已由用户手动停止。"

            # A. 上下文压缩（有效上限 = max(预算下限, 90% × 模型窗口)）
            #    超限优先 LLM 摘要化旧轮次（opencode 风格，前缀只失效一次）；
            #    摘要失败才回退策略 B 两阶段裁剪
            cap = self._effective_cap()
            if TokenCounter.count_messages_tokens(messages) > cap:
                compacted = await self._try_compact(messages, cap)
                if compacted is not None:
                    messages[:] = compacted
                    payload = messages
                else:
                    payload = self.context_manager.prune_messages(messages, hard_cap=cap)
                    if self.context_manager.last_prune.get("compressed"):
                        self._compression_count += 1
                        self._compressed_tokens += int(
                            self.context_manager.last_prune.get("removed_tokens", 0))
            else:
                payload = messages

            # B. beforeLLM 管道（插件可修改消息）
            processed = await self.kernel.before_llm.run(self.kernel.ctx, payload)
            # B2. 发送前兜底：修复工具调用原子对（停止/压缩/缺 id 可能破坏链）
            processed = repair_tool_call_pairs(processed)

            # D. 调用 LLM（流式，内部 emit llm:stream）
            content, tool_calls, usage = await self.adapter.chat_stream(
                processed, tools, self.kernel.events)

            # D2. 估算兜底 → 真实 usage 回填（缓存命中统计，详见第4课）
            if not self._last_usage:
                stats["input_tokens"] += TokenCounter.count_messages_tokens(processed)
            self._last_usage = usage or self._last_usage
            if usage:
                stats["input_tokens"] += usage.get("prompt_tokens", 0)
                stats["output_tokens"] += usage.get("completion_tokens", 0)
                hit = usage.get("prompt_cache_hit_tokens", 0)
                prompt = usage.get("prompt_tokens", 0)
                stats["cache_hit_tokens"] += hit
                if self.adapter.name == "anthropic":
                    # Anthropic: input_tokens 不含 cache_read，miss = input_tokens
                    stats["cache_miss_tokens"] += prompt
                else:
                    # OpenAI 兼容（DeepSeek 等）: prompt_tokens 已含命中部分
                    stats["cache_miss_tokens"] += max(0, prompt - hit)
            else:
                stats["output_tokens"] += TokenCounter.count_text_tokens(content or "")

            await self._emit_context_stats(stats)   # 推送「上下文情况」面板

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
                    result = truncate_tool_output(raw, output_dir=self.truncation_dir).content

                messages.append(Message(role="tool", name=call.name,
                    tool_call_id=call.id, content=result))
                stats["tool_calls"] += 1

            # H. 每轮批量执行完成后落盘
            if store_snapshot: self._save_session()

        return "[Loop Terminated]: 超出最大步骤限制。"

    def _effective_cap(self) -> int:
        """上下文有效上限 = max(预算下限, 90% × 模型窗口)（第3课）。

        大窗口模型（如 DeepSeek V4 1M）不被默认预算锁死——压缩延迟到
        90% × 窗口，避免任务中途频繁裁剪破坏缓存前缀。
        """
        budget = self.context_manager.max_allowed_tokens
        window_cap = int(0.9 * self.context_window)
        if self.context_window >= int(budget / 0.9):
            return window_cap
        return min(budget, window_cap)

    async def _try_compact(self, messages, cap):
        """opencode 风格压缩：旧轮次 LLM 摘要化，最近轮次原样保留。

        摘要替换只发生一次（前缀失效一次），此后前缀逐字节稳定 → 缓存命中延续；
        摘要失败回退策略 B 两阶段裁剪。
        """

    async def _emit_context_stats(self, stats: Dict[str, Any]) -> None:
        """推送「上下文情况」统计：缓存命中率 / 压缩次数 / 窗口占用比例。"""
        hit = stats.get("cache_hit_tokens", 0)
        miss = stats.get("cache_miss_tokens", 0)
        hit_rate = round(hit / (hit + miss), 4) if (hit + miss) > 0 else None
        prompt_tokens = (self._last_usage or {}).get("prompt_tokens") or stats.get("input_tokens", 0)
        usage_ratio = round(prompt_tokens / self.context_window, 4) if self.context_window else None
        await self.kernel.events.emit("context:stats", {
            "model": getattr(self.adapter, "model", None) or "",
            "context_window": self.context_window,
            "task": {
                "prompt_tokens": stats.get("input_tokens", 0),
                "output_tokens": stats.get("output_tokens", 0),
                "cache_hit_tokens": hit,
                "cache_miss_tokens": miss,
                "cache_hit_rate": hit_rate,
                "compression_count": self._compression_count,
                "compressed_tokens": self._compressed_tokens,
                "usage_ratio": usage_ratio,
                "last_prompt_tokens": prompt_tokens,
            },
        })
```

#### 5. 子 Agent 编排 (`litecode/orchestration/sub_agent.py`)

对应第 13 课的子 Agent 编排，在 lite-code 中真实实现：创建独立 Kernel 和 AgentLoop，工具集按角色裁剪（explorer/read-only 不赋予写文件权限）：

```python
class SubAgentRunner:
    async def run_task(self, task_description, role="general", max_steps=12):
        sub_kernel = Kernel(session_id=f"sub_{uuid.uuid4().hex[:8]}")
        # 按角色裁剪工具集：explorer/read-only 不赋予写文件权限
        allowed = ROLE_TOOLS.get(role)
        registry = self.app.build_registry(allowed=allowed, exclude=["spawn_sub_agent"])
        # 独立上下文 + 独立循环（usage 会通过 run_task 返回，供 Token 归集）
        loop = AgentLoop(
            kernel=sub_kernel, adapter=self.app.adapter,
            registry=registry, session_store=None,
            context_manager=ContextManager(max_allowed_tokens=24000),
            max_steps=max_steps,
            context_window=self.app.llm_registry.get_context_window(self.app.llm_registry.active),
            # 四级解析：手动覆盖 → models.dev → 内置表 → 128K（详见第 16 课 §5）
        )
        summary, _ = await loop.run_task(
            prompt=f"请完成以下子任务：\n{task_description}\n完成后只输出结论。",
            system_prompt=SUB_AGENT_SYSTEM_PROMPT,   # 角色化 system prompt
            tools=registry.tools,
            store_snapshot=False,                     # 子 Agent 会话不落盘
        )
        return {
            "summary": summary,
            "total_tokens_used": loop._last_usage.get("total_tokens", 0) if loop._last_usage else 0,
            "completed": loop.state.status == AgentStatus.SUCCESS,
        }
```

#### 本课小结

在本课中，我们实现了 `lite-code` 的灵魂模块——**AgentLoop 主循环**：

1. 掌握了完整的 **Think-Act-Observe 状态机** 控制逻辑；
2. 集成了第 2 课所有防御：**JSON 自愈**、**死循环 Hash 检测**、**输出截断**（截断结果落盘，上下文只放句柄），并新增**工具调用原子对修复**（恢复历史后 + 每次调用 LLM 前各跑一遍，残缺/无主/空 id 链一律不出站）；
3. 集成了第 3 课所有增强：**Token 预算估算**、**策略 B 两阶段滑动裁剪**（保护 system 与 tool 原子对、保留最近 K 轮、`max(预算下限, 90% × 模型窗口)` 有效上限）、**LLM 摘要化压缩**（opencode 风格：旧轮次摘要替换、最近轮次原样保留，前缀只失效一次）、**静态 System Prompt**（任务内构建一次，稳定前缀）；
4. 实现了 **beforeTool 安全管道**（SecurityPlugin 的接入点，插件本体在第 18 课实现）；
5. 实现了 **估算兜底 + 真实 usage 回填**（第 4 课）：用模型返回的 `prompt_cache_hit_tokens` 精确统计缓存命中率，并对 Anthropic 与 OpenAI 兼容接口使用不同的 miss 口径；
6. 实现了 **上下文可观测性**：`context:stats` 事件把压缩次数、压缩节省 Token、命中率、窗口占用比例推给「上下文情况」面板；
7. 主循环回收后自动**会话落盘**，防止中途异常崩溃丢状态；
8. 子 Agent 编排**真实化**：上下文隔离、只读工具裁剪、Token 归集。

下一次我们将开启 **第18课：安全沙箱与高危拦截实战 (`lite-code` 实战第四篇)** —— 给 `lite-code` 加入动态黑白名单、三级风险控制、Web 审批卡与提权确认机制！
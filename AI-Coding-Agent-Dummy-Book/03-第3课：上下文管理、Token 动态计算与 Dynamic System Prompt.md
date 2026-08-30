在实际的 Code Agent 开发中，随着对话轮数变多以及读取的文件变大，你很快就会遇到 **Context Window Overflow（上下文窗口溢出）** 报错。更糟糕的是，当上下文过长时，LLM 会出现"注意力幻觉"与**上下文腐败（Context Rot）**，开始忽视最开始的指令或报错信息。

本课我们将手写：
1. **轻量 Token 准确计数器** 与滑动窗口（Sliding Window）裁剪策略；
2. **Dynamic System Prompt 动态组装器**（注入环境状态、Git 分支、代码库摘要）；
3. **上下文保留策略**（在裁剪历史时，如何保留关键的 System Prompt 与最近的决策链）。

#### 1. Token 准确估算与计数器 (Token Counter)

在 Python 生态中，若需绝对精准可以引入 `tiktoken`。这里我们先封装一个零依赖的 `TokenCounter` 模块：

```python
# utils/token_counter.py
import re
from typing import List

_CJK_RE = re.compile(r"[\u4e00-\u9fa5]")

class TokenCounter:
    """
    简易且高性能的 Token 计数/估算逻辑。
    基础规则：1 Token ≈ 4 个英文字符 / 0.75 个中文字符，
    加上 Message 结构本身约 4 Token 损耗。
    """

    @staticmethod
    def count_text_tokens(text: str) -> int:
        cjk_count = len(_CJK_RE.findall(text))
        non_cjk_length = len(text) - cjk_count
        return max(1, int(cjk_count * 1.3 + non_cjk_length / 3.8))

    @classmethod
    def count_message_tokens(cls, message: Message) -> int:
        num = 4  # role, formatting 基础消耗
        if message.content:
            num += cls.count_text_tokens(message.content)
        if message.tool_calls:
            for call in message.tool_calls:
                num += cls.count_text_tokens(call.name)
                num += cls.count_text_tokens(call.arguments)
                num += 6  # tool_call 协议开销
        if message.tool_call_id:
            num += cls.count_text_tokens(message.tool_call_id)
        return num

    @classmethod
    def count_messages_tokens(cls, messages: List[Message]) -> int:
        # 加上 prompt 结尾开销
        return sum(cls.count_message_tokens(m) for m in messages) + 3
```

#### 2. 上下文滑动窗口裁剪 (Context Pruning / Sliding Window)

当 `messages` 的总 Token 超过预设阈值（例如 64k Token 或模型上限的 80%）时，我们必须对历史记录进行智能裁剪。

**关键约束（裁剪必须满足）：**
1. **Index 0 的 System Prompt 永远不能删**。
2. **`assistant(tool_calls)` 与紧跟其后的 `tool(result)` 必须作为一个原子不可分割的 Pair 存在**。如果删除了 `tool(result)` 却保留了 `assistant`，API 会直接抛出错误。

```python
# utils/context_manager.py
import logging
from typing import List, Optional

logger = logging.getLogger("context")

class ContextManager:
    """智能裁剪历史消息，确保不超框且不破坏 Tool Call 的配对完整性。"""

    def __init__(self, max_allowed_tokens: int = 32000):
        self.max_allowed_tokens = max_allowed_tokens

    def prune_messages(self, messages: List[Message]) -> List[Message]:
        current = TokenCounter.count_messages_tokens(messages)
        if current <= self.max_allowed_tokens:
            return messages

        logger.warning("[ContextManager] Exceeded token budget (%s/%s). Pruning...",
                       current, self.max_allowed_tokens)

        # 保护 Index 0 (System Prompt)
        system_message = messages[0] if messages and messages[0].role == "system" else None
        removable = messages[1:] if system_message else list(messages)

        # 从最早的历史消息开始尝试丢弃
        while (TokenCounter.count_messages_tokens(
                ([system_message] if system_message else []) + removable)
               > self.max_allowed_tokens and len(removable) > 2):
            first = removable[0]

            # 1. 遇到 assistant 带 tool_calls，必须同时丢弃后续对应的 tool 消息
            if first.role == "assistant" and first.tool_calls:
                tool_call_ids = {c.id for c in first.tool_calls}
                removable.pop(0)
                while (removable and removable[0].role == "tool"
                       and removable[0].tool_call_id in tool_call_ids):
                    removable.pop(0)
            else:
                # 普通的 user 或 assistant 消息，直接移除
                removable.pop(0)

        return ([system_message] if system_message else []) + removable
```

**上述逐条丢弃方案的局限**：从最老逐条丢弃虽然保证了 tool 对原子性，但有两个问题：

1. **粒度太粗**——它无法区分「某轮的工具调用细节」与「某轮的关键结论」，可能为了省 Token 把最近的决策链也一并删掉；
2. **效率低下**——每次 `pop` 后都要重新对整条链计数，是 O(n²) 操作。

因此我们引入 **策略 B（两阶段裁剪）**，以 user 消息为界把对话切成一轮一轮：

1. **Stage 1 压缩工具细节**：从最老的轮次开始，把 `assistant(tool_calls)` + `tool(result)` **原子对整体删除**，只保留该轮的 user 问题与最终回答（对话主干）。最近 `keep_recent_full_turns` 轮的完整细节**永不压缩**，保证当前任务连续性；
2. **Stage 2 整轮删除**：还不够就按轮从最老开始整轮删除，**最新一轮永不删**；
3. **兜底**：若正文被删空，强制保留 `system + 最新一轮`，绝不让请求变成孤儿。

实现上用「标记数组 + 先算后删」，把 O(n²) 降到 O(n)：

```python
# core/context_manager.py（策略 B 完整实现）
import logging
from typing import Dict, List, Optional

from .token_counter import TokenCounter
from .types import Message

logger = logging.getLogger("harness.context")

class ContextManager:
    def __init__(self, max_allowed_tokens: int = 48000, keep_recent_full_turns: int = 2):
        self.max_allowed_tokens = max_allowed_tokens
        self.keep_recent_full_turns = max(1, keep_recent_full_turns)
        # 最近一次裁剪的统计（供 UI「上下文情况」展示）
        self.last_prune: Dict[str, object] = {
            "compressed": False, "removed_tokens": 0, "stage": None,
        }

    def prune_messages(self, messages: List[Message], hard_cap: Optional[int] = None) -> List[Message]:
        cap = hard_cap or self.max_allowed_tokens
        self.last_prune = {"compressed": False, "removed_tokens": 0, "stage": None}

        current = TokenCounter.count_messages_tokens(messages)
        if current <= cap:
            return messages

        logger.warning("[ContextManager] Exceeded token budget (%s/%s). Pruning...", current, cap)

        system, body = self._split_body(messages)
        sys_tokens = TokenCounter.count_message_tokens(system) if system else 0
        tokens = [sys_tokens] + [TokenCounter.count_message_tokens(m) for m in body]
        removed = [False] * (len(body) + 1)
        total = sum(tokens)

        # 阶段1：压缩更早轮次的工具细节（assistant(tool_calls)+tool 原子对）
        if total > cap:
            droppable = self._stage1_candidates(body)
            for ai, tool_idxs in droppable:
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
    def _split_body(messages: List[Message]):
        """拆出 system 消息与对话正文。"""
        system = messages[0] if messages and messages[0].role == "system" else None
        body = messages[1:] if system else list(messages)
        return system, body

    @staticmethod
    def _turn_ranges(body: List[Message]) -> List[tuple]:
        """把正文按 user 消息切成轮次，返回 [(start, end_excl), ...]。"""
        turns: List[tuple] = []
        start: Optional[int] = None
        for i, m in enumerate(body):
            if m.role == "user":
                if start is not None:
                    turns.append((start, i))
                start = i
        if start is not None:
            turns.append((start, len(body)))
        # 异常数据（正文开头不是 user）：并入第一个轮次
        if turns and turns[0][0] != 0:
            turns[0] = (0, turns[0][1])
        elif not body:
            turns = [(0, len(body))]
        return turns

    def _stage1_candidates(self, body: List[Message]) -> List[tuple]:
        """返回可删除的 assistant(tool_calls)+tool 对位置，最老在前；跳过最近 K 轮。"""
        turns = self._turn_ranges(body)
        keep_from = max(0, len(turns) - self.keep_recent_full_turns)
        candidates: List[tuple] = []
        for turn_idx, (start, end) in enumerate(turns):
            if turn_idx >= keep_from:
                continue
            i = start
            while i < end:
                m = body[i]
                if m.role == "assistant" and m.tool_calls:
                    ids = {c.id for c in m.tool_calls}
                    tool_idxs: List[int] = []
                    j = i + 1
                    while j < end and body[j].role == "tool" and body[j].tool_call_id in ids:
                        tool_idxs.append(j)
                        j += 1
                    candidates.append((i, tool_idxs))
                    i = j
                else:
                    i += 1
        return candidates

    def _oldest_turn_ranges(self, body: List[Message]) -> List[tuple]:
        """整轮删除顺序：最老优先，最新一轮永不删。"""
        turns = self._turn_ranges(body)
        if len(turns) <= 1:
            return []
        return turns[:-1]

    def _newest_turn(self, body: List[Message]) -> tuple:
        turns = self._turn_ranges(body)
        return turns[-1] if turns else (0, len(body))
```

**为什么保留最近 K 轮？** 工具调用是链式推理（读了文件才能改、改了才能验证），删掉最近的决策链会让模型"失忆"，当前任务直接断线。`keep_recent_full_turns=2` 是实践中的经验值：既保住当前任务的连续推理，又能把更早的历史压缩成"对话主干"。

**有效上限：max(预算下限, 90% × 模型窗口)**。模型上下文窗口是"输入 + 输出"的总预算，输入不能占满——必须给模型回复预留空间。但预算**不能把大窗口模型锁死**：DeepSeek V4 有 1M 窗口，若按 48K 默认预算每轮裁剪，任务中途频繁删旧消息 = 缓存前缀反复打洞 = 命中率趋近于零。opencode 的实践是"只在接近模型上限时压缩一次"，因此实战中：

```python
def _effective_cap(self) -> int:
    """上下文有效上限 = max(预算下限, 90% × 模型上下文窗口)。"""
    budget = self.context_manager.max_allowed_tokens
    window_cap = int(0.9 * self.context_window)
    if self.context_window >= int(budget / 0.9):
        return window_cap          # 大窗口：压缩延迟到 90% × 窗口（opencode 同款）
    return min(budget, window_cap)  # 小窗口：预算兜底，保持 90% 安全边际
```

预留 10% 给输出（以及工具结果的瞬时波动），否则模型会被"挤"得只能输出几个 Token。此外，压缩手段也升级为**先 LLM 摘要化、后整轮裁剪**（第 17 课实现：旧轮次摘要替换、最近轮次原样保留，前缀只在压缩时失效一次）。

#### 3. 手写 Dynamic System Prompt 动态组装器

在真正的软件开发 Harness 中，System Prompt 需要**感知环境信息**——操作系统、工作目录、Git 状态、可用工具，这些上下文直接影响 Agent 的决策质量。

每次调用 LLM 前，Dynamic System Prompt 会实时收集：
- 当前工作目录（CWD）
- 当前 Git 分支与未提交的文件改动摘要
- 操作系统类型与可用 Shell
- 当前已经加载激活的 Tools 列表

> 此处的动态组装是**概念演示**——它揭示了"环境感知"的价值，也埋下了与缓存前缀的冲突（见下文「设计决策」）；我们在第 17 课的实战实现中会把它收敛为**静态骨架 + 工具按需获取**。

```python
# prompt/system_prompt.py
import os
import platform
import subprocess
from typing import List

class SystemPromptBuilder:
    """动态获取当前环境信息并生成 System Prompt。"""

    @staticmethod
    def _git_info(cwd: str) -> str:
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=cwd, capture_output=True, text=True, timeout=3)
            branch_name = branch.stdout.strip() if branch.returncode == 0 else "N/A"
            status = subprocess.run(
                ["git", "status", "--short"],
                cwd=cwd, capture_output=True, text=True, timeout=3)
            changed = len([l for l in status.stdout.splitlines() if l.strip()])
            return f"分支: {branch_name} | 未提交改动文件数: {changed}"
        except Exception:
            return "不是 Git 仓库 / Git 不可用"

    @classmethod
    def build(cls, cwd: str, tools: List[ToolDefinition]) -> str:
        os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"
        tools_summary = "\n".join(f"- **{t.name}**: {t.description}" for t in tools)

        return f"""你是一个专业的 AI 软件工程师 Code Agent，运行在用户本地的开发环境中。

### 环境信息 (Environment Context)
- **操作系统**: {os_name}
- **当前工作目录**: `{cwd}`
- **Git 状态**: {cls._git_info(cwd)}

### 可用工具 (Available Tools)
{tools_summary}

### 工作规则 (Operating Rules)
1. 修改代码前，先用工具探查代码库结构与相关文件内容，不要盲目猜测；
2. 修改文件使用精确编辑工具，避免整文件重写；
3. 命令失败时分析错误输出并换一种策略，不要连续用相同参数重试；
4. 用简洁的 Markdown 回复用户；中文优先。
"""
```

**设计决策：动态 System Prompt 与缓存红线的张力**

细心的读者会发现：`_git_info()` 的结果每次调用都在变（新增文件、`git commit` 后清零），如果每轮都用新文本**整体替换** `payload[0]`，那么 system 前缀就永远不稳定——这与第 5 课将强调的「缓存断点之前不能动一个字节」红线直接冲突。

本教程在第 17 课的实战实现中采用：**静态骨架 + 工具按需获取**。理由：

1. 动态内容并非都要进 System——`git_status`、`file_tree` 等工具返回的信息**永远比 System 里预埋的快照新鲜**，把"当前状态"交给工具查询，System 只保留角色、环境常量与规则；
2. 对缓存而言，system 前缀逐字节稳定是硬前提：Anthropic 断点标注与 DeepSeek 自动前缀缓存都要求**断点前一个字节都不能变**，每轮重渲染等于让整段前缀缓存永远 miss；
3. 真正会变化的环境信息（操作系统、工作目录、工具列表）在**单个任务内是恒定的**，放进 System 不影响前缀稳定性——只有 git 状态这类随工具执行而变的内容需要剥离。

这个取舍值得记录：**「保新鲜」还是「保缓存」**——答案不是二选一，而是**区分静态与动态**：静态部分进 System 吃缓存，动态部分交给工具实时查询。这与第 4 课将讲的「稳定前缀」原则一致，并会在第 17 课的 AgentLoop 中落地为静态 System Prompt。

#### 4. 集成：完整的上下文受控 Agent 循环

我们将 `TokenCounter`、`ContextManager` 以及 `SystemPromptBuilder` 整合进第二课的健壮循环中：

```python
# robust_agent_v2.py
import asyncio, os

async def run_context_aware_agent(user_prompt: str, tools, tool_executor):
    provider = LLMProvider(api_key=os.environ.get("DEEPSEEK_API_KEY", ""))
    context_manager = ContextManager(16000)  # 限制 Token 预算为 16k

    # 1. 动态生成 System Prompt
    system_prompt_content = SystemPromptBuilder.build(cwd=os.getcwd(), tools=tools)

    messages: List[Message] = [
        Message(role="system", content=system_prompt_content),
        Message(role="user", content=user_prompt),
    ]

    for turn in range(10):
        # 2. 关键步骤：发送前执行动态滑动裁剪，防爆框
        messages = context_manager.prune_messages(messages)
        print(f"\n--- [Turn {turn+1}] Context Size: {len(messages)} msgs ---")

        full_text = ""
        tool_calls: List[ToolCall] = []

        async for event in provider.chat_stream(messages, tools):
            if event["type"] == "text_delta":
                print(event["content"], end="", flush=True)
                full_text += event["content"]
            elif event["type"] == "tool_calls_update":
                tool_calls = event["toolCalls"]

        if not tool_calls:
            messages.append(Message(role="assistant", content=full_text))
            print("\n\n[Agent Completed Task]")
            break

        messages.append(Message(
            role="assistant", content=full_text or None, tool_calls=tool_calls))

        for call in tool_calls:
            ok, parsed, err = safe_json_parse(call.arguments)
            if not ok:
                messages.append(Message(role="tool", tool_call_id=call.id, content=err))
                continue
            try:
                raw = await tool_executor(call.name, parsed)
                messages.append(Message(role="tool", tool_call_id=call.id,
                                        content=truncate_tool_output(raw, 4000)))
            except Exception as e:
                messages.append(Message(role="tool", tool_call_id=call.id,
                                        content=f"Tool Error: {e}"))
```

### 本课小结

在本课中，我们完成了 Agent 底层控制循环的上下文建设：

1. 学会了纯手写流式 Tool Calling 拼接；
2. 实现了 JSON 自愈、死循环 Hash 预警与工具输出截断；
3. 掌握了 Token 估算、保护 `assistant-tool` 完整性的**策略 B 两阶段裁剪**算法，理解了感知环境的 System Prompt 设计，以及它与缓存前缀的张力与取舍（第 17 课实战将落地为静态骨架）。

在下一课中，我们将进入 **第4课：Prompt 缓存机制** —— 学习如何让稳定前缀命中供应商的 KV 缓存，把输入 Token 成本砍到 10%。
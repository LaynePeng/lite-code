在真实的 Code Agent 开发中，随着对话轮数变多以及读取的文件变大，你很快就会遇到 **Context Window Overflow（上下文窗口溢出）** 报错。更糟糕的是，当上下文过长时，LLM 会出现"注意力幻觉"与**上下文腐败（Context Rot）**，开始忽视最开始的指令或报错信息。

本课我们将手写：
1. **轻量 Token 准确计数器** 与滑动窗口（Sliding Window）裁剪策略；
2. **Dynamic System Prompt 动态组装器**（注入环境状态、Git 分支、代码库摘要）；
3. **上下文保留策略**（在裁剪历史时，如何保留关键的 System Prompt 与最近的决策链）。

#### 1. Token 准确估算与计数器 (Token Counter)

在 Python 生态中，若需绝对精准可以引入 `tiktoken`。这里我们先封装一个零依赖的 `TokenCounter` 模块，采用与实战一致的估算逻辑：

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

**关键约束（绝对不能违反）：**
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

#### 3. 手写 Dynamic System Prompt 动态组装器

在真实的软件开发 Harness 中，System Prompt **绝不是静态的字符串**，而是随环境动态渲染的。

每次调用 LLM 前，Dynamic System Prompt 会实时收集：
- 当前工作目录（CWD）
- 当前 Git 分支与未提交的文件改动摘要
- 操作系统类型与可用 Shell
- 当前已经加载激活的 Tools 列表

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

在第三课中，我们完成了 **模块一：Agent Harness 基础与底层控制循环** 的全路线建设：

1. 学会了纯手写流式 Tool Calling 拼接；
2. 实现了 JSON 自愈、死循环 Hash 预警与工具输出截断；
3. 掌握了 Token 估算、保护 `assistant-tool` 完整性的上下文裁剪算法，以及动态注入环境信息的 System Prompt。

下一次我们将进入 **第6课：代码感知、Ripgrep 高效搜索与代码库结构构建** —— 学习如何让 Agent 高效感知几十万行的真实代码库！
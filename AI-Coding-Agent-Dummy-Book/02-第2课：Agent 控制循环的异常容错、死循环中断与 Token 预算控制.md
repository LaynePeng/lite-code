在上节课中，我们写出了最基础的 Agent Loop。但在真实的 Code Agent 研发中，直接使用那个基础循环会遇到三个严重的工程崩溃点：

1. **JSON 语法解析崩溃**：模型返回的 `arguments` 可能缺括号、多逗号，或者被截断，导致 `json.loads` 直接抛错崩溃。
2. **死循环与震荡（Oscillation）**：Agent 陷入"报错 → 再次尝试同个错误工具 → 再次报错"的无限死循环，白白消耗费用。
3. **Token 爆炸与预算失控**：工具返回的文本（如日志或文件内容）过长，导致单次请求超过 LLM 上下文上限（Context Window Overflow）。

本课我们来**手写状态机、容错机制与预算控制器**，彻底解决这三个问题。

#### 1. JSON 修复与工具调用容错

当 `json.loads` 失败时，我们**不能让程序 crash**，也不能简单抛出异常给用户。

正确的做法是：**捕获解析错误，把错误信息作为 `tool` 角色的结果塞回给 LLM**，让 LLM 在下一个 Turn 自己纠正 JSON 格式。

```python
# utils/json_repair.py
import json
import re

def safe_json_parse(json_string: str):
    """尝试解析 JSON，返回 (success, data, error)。"""
    try:
        return True, json.loads(json_string), ""
    except Exception as first_err:
        # 如果直接 Parse 失败，尝试处理常见的前后残余字符
        cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", json_string).strip()
        try:
            return True, json.loads(cleaned), ""
        except Exception:
            return (
                False, None,
                f"JSON Parse Failed: {first_err}. Raw output was: \"{json_string[:500]}\"。"
                f"请将参数格式化为合法 JSON。",
            )
```

#### 2. Agent 状态机与检测震荡（Anti-Loop Counter）

为了防止 Agent 陷入无意义的循环，我们需要定义显式的 **Agent State**，并引入工具调用哈希追踪（Action Hash Tracking）。如果 Agent 连续 3 次传入完全相同的参数调用同一个工具，强行中断并要求其重新规划。

```python
# agent_state.py
from enum import Enum
from typing import List

class AgentStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED_MAX_TURNS = "FAILED_MAX_TURNS"
    FAILED_LOOP_DETECTED = "FAILED_LOOP_DETECTED"
    FAILED_BUDGET_EXCEEDED = "FAILED_BUDGET_EXCEEDED"

class AgentStateTracker:
    """检查是否陷入重复调用的死循环。"""

    def __init__(self, loop_threshold: int = 3):
        self.loop_threshold = loop_threshold
        self.history_action_hashes: List[str] = []
        self.status: AgentStatus = AgentStatus.IDLE

    def register_and_check_loop(self, tool_name: str, args_str: str) -> bool:
        action_hash = f"{tool_name}:{args_str.strip()}"
        self.history_action_hashes.append(action_hash)

        if len(self.history_action_hashes) >= self.loop_threshold:
            last = self.history_action_hashes[-self.loop_threshold:]
            if len(set(last)) == 1:
                self.status = AgentStatus.FAILED_LOOP_DETECTED
                return True  # 触发死循环预警
        return False
```

#### 3. Token 预算与工具输出截断器 (Tool Output Truncator)

Code Agent 最容易引发爆框的操作是 `read_file` 读了一个巨型文件，或者 Shell 执行命令打出了几万行日志。我们必须在工具返回结果进入上下文前，实现**强制截断与摘要防护**。

```python
# utils/truncator.py
def truncate_tool_output(output: str, max_characters: int = 8000) -> str:
    if len(output) <= max_characters:
        return output

    half = max_characters // 2
    head = output[:half]
    tail = output[-half:]
    omitted = len(output) - max_characters
    return f"{head}\n\n[... ⚠️ 内容已被 Harness 截断 ({omitted} 字符省略) ...]\n\n{tail}"
```

#### 4. 重构核心控制循环 (Robust Agent Loop)

结合上述防护手段，我们重写 Agent 的核心执行函数：

```python
# robust_agent.py
import asyncio, os, json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

@dataclass
class AgentConfig:
    max_turns: int = 10
    max_output_chars_per_tool: int = 8000
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"

async def run_robust_agent_loop(
    prompt: str,
    tools: List[ToolDefinition],
    tool_executor: Callable[[str, Any], str],
    config: AgentConfig,
) -> Tuple[AgentStatus, int, List[Message]]:
    provider = LLMProvider(config.api_key, config.base_url)
    state_tracker = AgentStateTracker()
    state_tracker.status = AgentStatus.RUNNING

    messages: List[Message] = [
        Message(role="system", content="你是一个专业的 Code Agent。每次调用工具后请仔细分析结果。"),
        Message(role="user", content=prompt),
    ]

    turn_count = 0

    while state_tracker.status == AgentStatus.RUNNING:
        turn_count += 1
        if turn_count > config.max_turns:
            state_tracker.status = AgentStatus.FAILED_MAX_TURNS
            print(f"\n[Harness Error]: Max turns reached ({config.max_turns}). Interrupted.")
            break

        print(f"\n--- [Turn {turn_count}/{config.max_turns}] ---")

        full_text = ""
        final_tool_calls: List[ToolCall] = []

        # 1. 调用 LLM
        try:
            async for event in provider.chat_stream(messages, tools):
                if event["type"] == "text_delta":
                    print(event["content"], end="", flush=True)
                    full_text += event["content"]
                elif event["type"] == "tool_calls_update":
                    final_tool_calls = event["toolCalls"]
        except Exception as e:
            print(f"\n[LLM API Error]: {e}")
            continue  # API 临时报错，重试

        # 2. 无 Tool Call 表示 Agent 思考完毕输出最终结果
        if not final_tool_calls:
            messages.append(Message(role="assistant", content=full_text))
            state_tracker.status = AgentStatus.SUCCESS
            print("\n\n[Task Finished Successfully]")
            break

        # 3. 记录 Assistant 状态
        messages.append(Message(
            role="assistant", content=full_text or None,
            tool_calls=final_tool_calls,
        ))

        # 4. 执行 Tools（带死循环检测与 JSON 容错）
        for tool_call in final_tool_calls:
            tool_name = tool_call.name
            raw_args = tool_call.arguments

            print(f"\n[Tool Executing]: {tool_name}")

            # 4.1 死循环检查
            if state_tracker.register_and_check_loop(tool_name, raw_args):
                print(f"\n[Harness Defense]: Infinite loop detected on tool \"{tool_name}\". Interrupting.")
                messages.append(Message(
                    role="tool", tool_call_id=tool_call.id,
                    content=f"[Harness Error]: Infinite loop detected! "
                            f"You have called {tool_name} with identical parameters "
                            f"{state_tracker.loop_threshold} times in a row.",
                ))
                break

            # 4.2 JSON 解析容错
            ok, parsed_args, error = safe_json_parse(raw_args)
            if not ok:
                print(f"\n[Harness Defense]: JSON Parse error. Feeding back to LLM.")
                messages.append(Message(
                    role="tool", tool_call_id=tool_call.id, content=error,
                ))
                continue

            # 4.3 执行工具 & 输出截断
            try:
                raw_output = await tool_executor(tool_name, parsed_args)
                safe_output = truncate_tool_output(raw_output, config.max_output_chars_per_tool)
                messages.append(Message(
                    role="tool", tool_call_id=tool_call.id, content=safe_output,
                ))
            except Exception as e:
                messages.append(Message(
                    role="tool", tool_call_id=tool_call.id,
                    content=f"[Tool Execution Error]: {e}",
                ))

    return state_tracker.status, turn_count, messages
```

### 本课小结

在这节课中，我们补齐了底层控制循环的核心防御机制：
- 通过 **JSON Parsing Retry** 让 LLM 具有自我修复能力；
- 通过 **Action Hash Tracking** 杜绝 Agent 的卡死与陷入死循环；
- **Truncator** 防范工具输出过大导致的 Token 爆框。

在下一课中，我们将离开抽象的控制循环，进入 **"代码库感知"** —— 学习如何让 Harness 高效读取几十万行的真实项目代码，实现**滑动窗口**与 **System Prompt 动态上下文组装**。
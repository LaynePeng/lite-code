# 第 19 课：人机交互——审批卡与 ask_user 提问

## 本课定位

Agent 在自主执行中会遇到需要**用户介入**的场景：
- **审批**（第 18 课已覆盖）：高危操作需要用户二值决策（允许/拒绝）；
- **提问**（本课新增）：Agent 需要用户提供信息才能继续——"这个目录下有两个 `.env` 文件，应该用哪个？"。

审批是"二值决策"，提问是"开放信息收集"。两者机制相似（都是 `asyncio.Future` 挂起），但语义不同，实现细节也不同。本课重点讲提问（`ask_user`），并对比审批与提问的差异。

## 一、Agent 为什么需要"暂停等待用户"

### 1.1 asyncio.Future 挂起模型

AgentLoop 本质是一个异步循环。常规执行流是：

```
LLM 调用 → 解析工具 → 执行工具 → 回填 → 再次 LLM 调用
```

但有些工具的执行结果**不能由 Agent 自身决定**——需要用户提供信息。此时工具执行器不返回结果，而是**挂起当前协程**，等待用户响应后再继续：

```python
# 伪代码：挂起模型
async def ask_user_handler(args):
    future = asyncio.get_event_loop().create_future()
    # 把 future 存起来，等待用户回答
    answer = await future   # 这里挂起，不阻塞其他协程
    return f"[用户回答] {answer}"
```

`await future` 会**挂起当前协程**，但不阻塞事件循环——其他任务（如 SSE 推送、心跳）继续运行。当用户在 Web UI 上点击"提交"，后端通过 `POST /api/answer` 调用 `future.set_result(answer)`，挂起的协程被唤醒，继续执行。

### 1.2 与 ApprovalGate 的异同

| 维度 | ApprovalGate（审批） | QuestionGate（提问） |
|---|---|---|
| 输入 | `action` + `reason` | `question` + `options[]` |
| 输出 | `bool`（允许/拒绝） | `str`（用户回答） |
| 超时 | 超时自动拒绝 | 超时返回超时提示 |
| 前端 | 弹窗（二选一） | 提问条（选项点选/自定义输入） |

## 二、QuestionGate：提问门

`litecode/security/question.py` 实现与 `ApprovalGate` 类似但输出为字符串：

```python
# question.py（核心）
class QuestionGate:
    def __init__(self, timeout_seconds: float = 600.0):
        self.timeout_seconds = timeout_seconds
        self._ids = itertools.count(1)
        self._pending: Dict[str, Dict[str, Any]] = {}

    def request(self, question: str, options=None) -> asyncio.Future:
        qid = f"q_{next(self._ids)}"
        future = asyncio.get_event_loop().create_future()
        self._pending[qid] = {
            "id": qid, "question": question,
            "options": options or [],
            "created_at": int(time.time() * 1000),
            "future": future,
        }
        # 超时保护
        async def _timeout_guard():
            await asyncio.sleep(self.timeout_seconds)
            if not future.done():
                self.resolve(qid, "[Timeout] 等待回答超时", by="timeout")
        asyncio.ensure_future(_timeout_guard())
        return future

    def resolve(self, qid: str, answer: str, by: str = "user") -> bool:
        entry = self._pending.pop(qid, None)
        if entry is None:
            return False
        if not entry["future"].done():
            entry["future"].set_result(answer)   # 字符串，不是 bool
        return True
```

**关键区别**：`ApprovalGate.resolve` 传入 `bool`（`approved`），而 `QuestionGate.resolve` 传入 `str`（`answer`）。这个差异影响了前端组件、事件格式和超时处理。

## 三、ask_user 工具

### 3.1 工具插件定义

`litecode/tools/ask.py` 以 `ToolPlugin` 模式注册 `ask_user` 工具：

```python
# ask.py（核心）
class QuestionPlugin(ToolPlugin):
    name = "question-plugin"

    def get_tools(self):
        return [ToolDefinition(
            name="ask_user",
            description=(
                "向用户提问，并等待用户回答。"
                "可提供选项列表供用户选择，用户也可输入自定义回答。"
                "多个问题可同时提出，用户会逐个回答。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "问题内容"},
                    "options": {
                        "type": "array", "items": {"type": "string"},
                        "description": "可选选项列表",
                    },
                },
                "required": ["question"],
            },
        )]
```

### 3.2 工厂函数模式：make_ask_user_handler

`ask_user` 的工具处理器不是硬编码在插件里的——它通过**工厂函数**注入真实事件总线：

```python
# ask.py（核心）
def make_ask_user_handler(question_gate, events=None):
    """构造 ask_user 工具处理器。events 为任务 kernel 的事件总线。"""

    async def _handler(args):
        question = str(args.get("question", "")).strip()
        options = args.get("options") or []

        future = question_gate.request(question, options)
        qid = question_gate.current_id(future)

        # 向 UI 广播提问请求
        if events is not None:
            await events.emit("question:request", {
                "id": qid, "question": question, "options": options,
            })

        answer = await future   # 挂起，等待用户回答

        if events is not None:
            await events.emit("question:resolved", {"id": qid, "answer": answer})

        return f"[用户回答] {answer}"

    return _handler
```

### 3.3 create_kernel 注入

`app.py` 在装配真实任务内核时，通过 `set_handler` 注入携带 `kernel.events` 的 handler：

```python
# app.py（核心，约 414-419 行）
if registry.has("ask_user"):
    from .tools.ask import make_ask_user_handler
    # 注入真实事件总线（build_registry 引导阶段 events 为 None）
    plugin.set_handler("ask_user", make_ask_user_handler(
        self.question_gate, kernel.events))
```

这个设计模式与 `spawn_sub_agent` 相同：**工具定义在引导阶段注册，handler 在任务启动时注入**。这样工具定义可以被 `ToolRegistry` 统一管理，而 handler 里使用的事件总线、会话 ID 等上下文在任务启动时才确定。

## 四、前端提问条

### 4.1 非阻塞 inline 设计

与审批卡不同，**提问条不弹窗**——它固定在输入区上方：

```
┌───────────────────────────────────────┐
│ ❓ Agent 需要你的回答                   │
│ ┌─ 问题 1 ── 问题 2 ──┐  (多问题 Tab) │
│                                       │
│ 请选择使用哪个 API 端点：               │
│ [生产环境]  [测试环境]  [自定义]        │
│ ┌──────────────────┐ [提交]           │
│ │ 输入自定义回答…    │                │
│ └──────────────────┘                  │
└───────────────────────────────────────┘
├── Agent 选择 [Build/Plan] ────────────┤
├── 输入框                               │
└───────────────────────────────────────┘
```

**为什么不是弹窗？** 弹窗会遮挡对话历史，用户无法滚动查看上下文来回答。提问条**不遮挡对话**，用户可以边看历史边回答，回答完提问条自动消失。

### 4.2 多问题 Tab

Agent 一次可以提出多个问题（`ask_user` 被多次调用），前端用 Tab 切换：

```tsx
{questions.length > 1 && (
  <div className="question-tabs">
    {questions.map((q, i) => (
      <button key={q.id}
        className={`question-tab ${i === activeIdx ? "active" : ""}`}
        onClick={() => setActiveIdx(i)}>
        问题 {i + 1}
      </button>
    ))}
  </div>
)}
```

### 4.3 选项点选 + 自定义输入

每个问题支持：
- **选项点选**：`options[]` 不为空时显示按钮，点击直接提交回答；
- **自定义输入**：输入框 + 提交按钮，用户可输入选项以外的回答。

```tsx
{options.length > 0 && (
  <div className="question-options">
    {options.map((opt, i) => (
      <button key={i} onClick={() => onAnswer(id, opt)}>{opt}</button>
    ))}
  </div>
)}
<div className="question-custom-row">
  <input placeholder="输入自定义回答…" value={customAnswer}
    onChange={e => setCustomAnswer(e.target.value)} />
  <button disabled={!customAnswer.trim()} onClick={() => ...}>提交回答</button>
</div>
```

## 五、审批 vs 提问：对比总结

| 维度 | 审批（ApprovalGate） | 提问（QuestionGate） |
|---|---|---|
| 触发时机 | 中危操作（MCP 工具、rm 等） | Agent 需要用户提供信息 |
| 输出类型 | `bool`（允许/拒绝） | `str`（用户回答） |
| 前端组件 | 弹窗（modal） | 提问条（inline bar） |
| 超时行为 | 默认拒绝 | 返回超时提示 |
| 多问题 | 不适用（一次一个） | Tab 切换 |
| 选项 | 仅"允许/拒绝" | 自定义选项列表 + 自由输入 |
| Plan Agent | 不开放（只读代理不执行工具） | **开放**（规划需要信息收集） |

**为什么 Plan Agent 可以 ask_user？** Plan Agent 的工具集是只读白名单（`read_file`、`search_code`、`git_status`、`todo_write`、`ask_user`）。`ask_user` 是只读的——它只是等用户给个回答，不修改任何文件。Plan Agent 可能在规划阶段需要用户确认："这个重构涉及 3 个模块，是按模块拆分还是按功能拆分？"——没有用户的输入，计划可能走向错误方向。所以开放 `ask_user` 给 Plan Agent 是合理的。

## 本课小结

1. **asyncio.Future 挂起模型**：`await future` 挂起当前协程，不阻塞事件循环，用户响应后 `set_result` 唤醒；
2. **QuestionGate**：与 ApprovalGate 类似，但输出是字符串（用户回答）而非 bool（二值决策）；
3. **ask_user 工具**：`ToolPlugin` 注册 + 工厂函数注入事件总线；
4. **前端提问条**：非阻塞 inline 设计，固定在输入区上方，不遮挡对话；多问题 Tab、选项点选、自定义输入；
5. **Plan Agent 的 ask_user 支持**：只读白名单含 `ask_user`，规划阶段可以收集信息。

下一篇进入 **第 20 课：TODO 清单系统**——学习如何通过 `todo_write` 工具让 Agent 维护多步骤任务的进度看板。
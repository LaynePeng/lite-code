在前面的课程中，我们从架构设计、核心 Kernel、插件体系、AgentLoop 状态机到沙箱防护，一步步完成了 `lite-code` 框架的底层构建。

本课开始手写实战的**外壳篇**，我们先为 `lite-code` 构建完整的 Web UI：

1. **FastAPI 服务层**：REST API + SSE 流式推送，连接前后端；
2. **React 现代化 Web UI**：流式 Markdown、工具调用卡片、审批弹窗、会话/文件树/成本面板；
3. **上下文可观测性**：窗口占用、缓存命中率、压缩统计实时面板；
4. **多 LLM 配置界面**：DeepSeek / OpenAI / Anthropic / 通义千问等多供应商切换；
5. **Web 侧稳定性加固**：请求超时、卡死检测、断连重连、渲染兜底。

Electron 桌面外壳与打包发布留到下一课。

#### 1. 架构总览

```text
+------------------------------------------------------------+
|                    Electron 桌面外壳                        |
|  +------------------------------------------------------+  |
|  |  React Web UI (Electron BrowserWindow)                 |  |
|  |  ┌──────────────────────────────────────────────────┐ |  |
|  |  │ 左栏：会话/文件树/成本  │ 主区：聊天/流式/审批  │ |  |
|  |  └──────────────────────────────────────────────────┘ |  |
|  +------------------------------------------------------+  |
|              | HTTP + SSE (127.0.0.1:随机端口)              |
|              v                                              |
|  +------------------------------------------------------+  |
|  |  Python FastAPI 后端 (核心)                           |  |
|  |  Core/AgentLoop/Tools/Security/Sessions/LLM 多供应商  |  |
|  +------------------------------------------------------+  |
+------------------------------------------------------------+
```

三种运行形态：
1. **本地桌面**：`npm start` 或 `npm run dev`，Electron 为每个本地项目窗口自动 spawn 独立 Python Core
2. **远程 Core**：`~/.lite-code/client.json` 配置 `coreUrl`，窗口直连远程服务器
3. **纯浏览器**：`python -m litecode serve` 后访问 `http://127.0.0.1:8787`

本课的所有内容（FastAPI + React）在三种形态下完全复用——**Web UI 不感知自己跑在浏览器还是 Electron 里**，桌面能力（终端、多窗口）通过 preload 桥按需注入，这是下一课的主题。

#### 2. FastAPI 服务层 (`litecode/server/app.py`)

基于 FastAPI 构建 REST + SSE 服务，前后端通过 HTTP 通信：

```python
# 创建 FastAPI 应用（核心代码）
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="lite-code")
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)

# 核心 API 路由
@app.get("/api/status")          # 服务状态、模型、工作区
@app.get("/api/sessions")        # 会话列表
@app.post("/api/chat")           # 启动任务
@app.get("/api/tasks/{id}/events")  # SSE 流式事件
@app.post("/api/approve")        # 审批确认
@app.post("/api/llm/config")     # LLM 配置更新
@app.post("/api/llm/test")       # 测试连接
@app.get("/api/context/stats")   # 会话级上下文统计（切换会话时回填面板）
@app.get("/api/workspace/tree")  # 文件树（旧：文本 ASCII 树）
@app.get("/api/workspace/tree-json")  # 结构化目录树：按路径懒加载 + git 状态字母
@app.get("/api/fs/read")         # 读文件内容 + 语言检测 + git diff（文件 Tab）
@app.get("/api/workspace/diff")  # 单文件 git diff（工作区 vs HEAD）
```

**文件读取与 diff（文件 Tab 的数据源）**：`/api/fs/read` 返回文件内容、语言（按扩展名映射）、行数，并附带该文件的 git diff；`/api/workspace/diff` 单独返回 diff 与增删行数。两者都做了**路径越界防护**——必须位于工作区内：

```python
# server/app.py（核心）
@app.get("/api/fs/read")
async def fs_read(path: str, request: Request = None):
    if request: _check_auth(request)
    import os as _os
    target = _os.path.abspath(_os.path.join(app.workspace, path))
    if not (target == app.workspace or target.startswith(app.workspace + _os.sep)):
        raise HTTPException(status_code=403, detail="路径越界")
    if not _os.path.isfile(target):
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
    content = open(target, encoding="utf-8", errors="replace").read()
    language = _detect_language(_os.path.splitext(path)[1])  # 扩展名 → 语言
    diff_text = _git_diff(app.workspace, path)               # git diff HEAD -- <path>
    return {"path": path, "content": content, "language": language,
            "lines": len(content.split("\n")), "size": len(content), "diff": diff_text}
```

**"先打开项目"是一等状态**：桌面应用启动时可以没有项目，`AgentApp.workspace` 允许为 `None`（不再静默把应用 cwd 当工作区）。所有依赖工作区的接口（会话、任务、文件树、文件读取）统一走 `_require_workspace()`——未打开项目时返回 409，前端引导用户先打开项目。这避免了"Agent 在错误目录里执行任务"这类难以察觉的事故：

```python
# server/app.py（核心）
def _require_workspace() -> str:
    if not app.workspace:
        raise HTTPException(status_code=409, detail="请先打开项目后再创建会话或执行任务")
    return app.workspace
```

**SSE 流式推送**：每个任务创建独立的 `asyncio.Queue`，AgentLoop 运行时通过 `TypedEventBus` 广播事件（`llm:stream`、`tool:before_execute`、`approval:request` 等），`TaskHandle` 订阅事件并推送到队列，SSE 端点从队列消费：

```python
async def _stream():
    while True:
        item = await asyncio.wait_for(handle.queue.get(), timeout=15)
        yield f"data: {json.dumps(item)}\n\n"

return StreamingResponse(_stream(), media_type="text/event-stream")
```

**流式文本完整性：不要用 React state 作为增量缓冲区**：SSE 事件可能在一次浏览器任务中连续到达，而 React 的 `setState` 是异步且会批处理更新。如果每个事件都从旧 state 读取内容，后一个 chunk 会基于旧值拼接并覆盖前一个 chunk：

```tsx
// 错误示例：高频 SSE 到达时，content 可能仍是旧值
const [content, setContent] = useState("");

function onStream(chunk: string) {
  setContent(content + chunk); // 闭包里的 content 不是最新值
}
```

实时显示内容与任务结束后固化的完整消息可能出现不一致，因此实时副本应使用独立的、不会受 React 渲染时序影响的累积缓冲区。

正确做法是将 `useRef` 作为实时缓冲区，React state 只负责定时刷新画面：

```tsx
const streamingRef = useRef({ content: "", cards: [] });
const flushTimerRef = useRef<number | null>(null);

function onStream(chunk: string) {
  const current = streamingRef.current;
  streamingRef.current = {
    ...current,
    content: current.content + chunk,
  };

  if (flushTimerRef.current === null) {
    flushTimerRef.current = window.setTimeout(() => {
      flushTimerRef.current = null;
      setStreaming({ ...streamingRef.current });
    }, 80);
  }
}
```

工具开始、工具完成、轮次切换等事件也必须从同一个 ref 读取最新对象，不能分别从 React state 读取，否则工具卡片更新仍可能把思考文本回退到旧版本。任务结束时，再使用后端完整消息或当前 ref 快照固化最终内容。

**流式工作时间线：不要把文本和工具拆成两个列表**。如果始终把“思考文本”渲染在工具列表上方，模型在工具执行前后的输出就会发生视觉跳位。实时副本应保存有序的工作项：文本片段和工具项共用一个 `items` 数组；文本 chunk 追加到末尾文本项，工具开始时插入工具项，工具完成时按 `id` 更新原项。历史消息也使用同样的顺序构建，这样实时视图和固化后的视图保持一致：

```tsx
type WorkItem =
  | { type: "text"; id: string; content: string }
  | { type: "tool"; id: string; card: ToolCardInfo }
  | { type: "activity"; id: string; tools: ToolCardInfo[] };

// 文本 → 工具 → 文本会严格保持这个顺序
const items = [
  { type: "text", id: "a", content: "先检查项目结构" },
  { type: "tool", id: "b", card: runningTool },
  { type: "text", id: "c", content: "检查完成，开始修改" },
];
```

普通工具项默认压缩为单行 activity，连续的 `read_file`、`search_code`、`list_dir` 等调用可以合并显示；文件修改 diff、异常和取消事件再使用展开卡片。这个分层同时解决了信息密度和可追溯性问题。

**SSE 背压与终止事件优先级**：事件生产速度可能高于浏览器消费速度，因此任务队列必须定义丢弃策略。中间的文本增量、进度和普通工具状态可以丢弃旧帧或合并；`task:done`、`task:error` 等终止事件不能丢弃，也不能因为队列已满而阻塞任务收尾。前端只有收到终止事件，才能清理运行状态、关闭流并恢复输入控件。

```python
def forward(event):
    if queue.full():
        queue.get_nowait()       # 丢弃旧的中间事件
    queue.put_nowait(event)

async def finish():
    while queue.full():
        queue.get_nowait()
    queue.put_nowait({"type": "task:done", "data": result})
```

实际实现还应对 `task:error` 使用相同的终止事件优先级，并在任务 finally 阶段发送 SSE 结束哨兵。这样网络断连、事件突发或 UI 暂时变慢时，任务仍能最终收敛。

这个原则也适用于工具参数增量、日志流、进度事件等所有高频 SSE 数据：**可变的实时累积数据放在 ref，React state 保存可渲染快照**。

**多轮对话历史加载**：每次任务都会新建 Kernel，如果从空上下文开始，新任务的落盘会覆盖上一轮历史。`TaskManager.start` 在启动任务前先从 `SessionStore` 恢复该会话的历史消息，再注入新 Kernel：

```python
# litecode/server/tasks.py（核心）
def start(self, session_id, prompt, agent_id=None):
    # 按 Agent 配置裁剪工具集（build 全量 / plan 只读 / 自定义）
    registry = self.app.create_agent_registry(agent_id or "build")
    # Cordis 内核装配：工具插件 + 安全插件全部挂载，tools 服务与 registry 一致
    kernel = self.app.create_kernel(session_id, registry=registry)
    snapshot = self.app.session_store.load(session_id)   # 恢复历史
    if snapshot and snapshot.messages:
        kernel.ctx.messages = list(snapshot.messages)    # 续上上一轮对话
    loop = self.app.create_loop(kernel, registry, agent_id=agent_id)
    ...
```

这样「第二轮」的上下文 = 第一轮完整历史 + 新问题，system + tools 前缀保持不变——多轮对话间的缓存命中也能延续（第 4 课稳定前缀 + 第 18 课静态 System Prompt）。

#### 3. React 前端（Web UI）

前端基于 **React 18 + Vite + TypeScript**，通过 SSE 与后端交互，实现实时流式渲染。

**核心组件**：

```text
web/src/
├── App.tsx            # 主应用：状态管理 + SSE 事件处理
├── api.ts             # API 客户端封装
├── types.ts           # 类型定义
├── styles.css         # 深色主题样式
└── components/
    ├── ChatView.tsx       # 聊天区：流式 Markdown + 工具卡片 + 审批
    ├── Composer.tsx       # 输入框
    ├── FileDiff.tsx       # 文件修改 diff 渲染（DiffStats / DiffPre）
    ├── Sidebar.tsx        # 侧边栏：会话/文件/终端（含真实目录树 + git 状态徽标）
    ├── SettingsModal.tsx  # 设置弹窗（LLM / MCP Server 双 Tab）
    └── ToolPanel.tsx      # 右侧面板：上下文/MCP/工具 三 Tab
```

**侧边栏「文件」页签（真实目录树 + git 状态）**：对标 OpenCode 的左侧文件树——

- **真实交互树**：目录可展开/折叠，子目录懒加载（点开才请求 `/api/workspace/tree-json?path=…`），目录在前、文件在后按名排序，gitignore 感知自动过滤 node_modules/.venv 等；
- **git 状态徽标**：后端 `litecode/server/tree.py` 解析 `git status --porcelain -z` → 每个文件显示状态字母，配色对齐 OpenCode——**M 黄（修改）/ A 绿（新增）/ D 红（删除，划线显示）/ U 灰（未跟踪）/ R 青（重命名）/ C 紫（冲突）**；已删除文件仍显示在树中；目录内含改动时显示高亮圆点；头部展示当前分支；git 状态带 3 秒 TTL 缓存避免频繁刷新拉起 git 进程；
- **动态刷新**：`App.tsx` 监听 SSE——写工具执行完（`write_file` / `apply_search_replace` / `apply_unified_diff` / `execute_command` / `git_commit`）、任务结束、子 Agent 完成时递增 `treeRevision`，`Sidebar` 的 `FileTree` 收到后自动重拉所有已展开目录，无需手动刷新。

**SSE 事件处理流程**（`App.tsx`）：

```javascript
// 建立 EventSource 连接
const es = new EventSource(`/api/tasks/${task_id}/events`);
es.onmessage = (e) => {
  if (e.data === "[DONE]") return;
  const ev = JSON.parse(e.data);
  switch (ev.type) {
    case "llm:stream":     // 流式文本增量附加到 streamingContent
    case "tool:before_execute":  // 增加工具卡片（运行中动画）
    case "tool:after_execute":   // 标记完成 + 耗时
    case "approval:request":     // 弹出审批卡
    case "approval:resolved":    // 关闭审批卡
    case "task:done":           // 消息固化 + 刷新会话
  }
};
```

**工具卡片**（`ToolCard`）：显示工具名、参数预览、状态（运行中/完成/已取消）和耗时，可展开查看完整参数与结果。

**审批卡**：`MEDIUM` 风险操作触发时，SSE 推送 `approval:request`，UI 弹窗显示操作详情与"允许/拒绝"按钮，点击后 `POST /api/approve` 解挂 Async Future。

**深色主题**（`styles.css`）：VS Code 暗色基底 + 蓝紫渐变强调色，256 行 CSS 自定义样式。

#### 3.5 交互设计

UI 采用以下交互约定：

**工具卡片与右侧面板的分工**：工具调用卡片直接渲染在对话流中（`ToolCard`，位于对应任务的 thinking 块下方），与消息上下文紧邻，便于对照"调用了什么 → 得到什么 → 得出什么结论"。右侧独立面板（`ToolPanel.tsx`）是**三 Tab 工作台**：**上下文**（窗口占用、缓存命中率、压缩统计、工具调用、安全拦截、预估成本——本次调用与会话累计两个视角）、**MCP**（服务器连接状态与工具数）、**工具**（当前 Agent 的已注册工具清单）：

```text
┌─────── 对话区 ───────┐  ┌─ [上下文|MCP|工具] ─┐
│ 用户：帮我查启动原因    │  │ 模型 · 窗口 128K     │
│                       │  │ ▓▓▓▓░░ 42%          │
│ 思考过程 ▸             │  │ 命中率 87%           │
│ [⚡ file_tree] 结果…   │  │ 压缩 2 次/节省…      │
│ [⚡ git_status] 结果…  │  │ 工具调用 6 次        │
│ [⚡ read_file] 结果…   │  │ 预估成本 $0.0123     │
│ ⚡ 分析完成。          │  │ ── 会话累计 ──        │
│ 以下是完整报告…         │  │ …                   │
└───────────────────────┘  └─────────────────────┘
```

工具卡片默认收起（仅显示工具名与状态），点击展开查看参数与结果；文件修改类（`[Patch Success]` 回执）卡片默认展开并渲染 opencode 风格 diff（见第 9 课）。

**统计为什么并入上下文面板**：早期的侧边栏有独立「统计」页签（输入/输出 tokens、工具调用、安全拦截、预估成本 + 已注册工具），但它展示的与上下文面板同源同口径（都来自 AgentLoop 的 stats）——拆两处既重复又割裂。最终形态：数字指标全部并入「上下文」Tab 的本次调用/会话累计两段，已注册工具独立成「工具」Tab（它属于"能力清单"而非统计），侧边栏腾出的位置给了**终端**（终端从右面板底部迁到侧边栏 Tab，每窗口一个实例，生命周期跟随窗口——打开项目/关窗时自动回收）。

**思考与回答分离**：模型调用工具时产生的中间内容归入一个 **thinking 块**（`<details>` 折叠），最终回答作为正常气泡显示。`buildTurns` 按每个 user 消息组织对应的 thinking 与回答：

```javascript
function buildTurns(messages) {
  // 每个 user 消息对应一个 thinking 块
  // 所有 assistant+tool_calls 的 content 累积到 thinking
  // 最终回答（assistant 无 tool_calls）作为独立气泡
}
```

**Tab 快捷键切换 Agent**：全局 `keydown` 监听，按 Tab 在 Build/Plan 间循环切换，`preventDefault` 阻止焦点跳转——输入框始终聚焦，可连续打字 + 切 Agent + 发送。

**停止按钮合一**：任务运行时，发送按钮（➤）变为停止按钮（■）。前端保存当前 `task_id`，调用 `POST /api/tasks/{task_id}/stop`；后端 `TaskHandle.stop()` 先设置 `abort_event`，再取消挂起的 asyncio 任务。若用户在 `POST /api/chat` 返回 task ID 前点击停止，前端会记录停止意图，拿到 ID 后立即补发停止请求；任务结束后清理旧 ID，避免误停下一任务。

**目录树打开项目**：浏览器模式下，点击"打开项目"弹出 `ProjectPicker` 目录树选择器，通过 `/api/fs/list` 后端接口逐层浏览文件系统，选完后自动切换工作区并跳转到文件 tab。

**单换行与表格美化**：通过 `remark-breaks` 插件让 Markdown 单换行（`\n`）渲染为 `<br>`，步骤性输出自然分行。表格加 `display: block + overflow-x: auto`，超宽表格横向滚动。

**Session 首句标题**：会话标题取首条用户消息的前 40 字；如果 metadata 中有显式名称则优先使用，空会话显示为“新会话”。没有用户消息的 session 不进入历史列表，列表接口会过滤并清理异常残留。

**Build/Plan 前端切换**：`Composer.tsx` 加入 Agent 选择栏，显示 Build / Plan 两个按钮，当前选中的高亮。右侧显示 `Tab` 小标签提示快捷键。

**后端新端点**：配套新增 `/api/agents` 返回 Agent 列表、`/api/workspace` 运行时切换工作区、`/api/fs/list` 浏览任意目录。

**MCP 工具**：最终项目通过 `litecode/mcp/` 提供 stdio MCP Client（第 11 课详细讲解实现）。配置持久化在 `~/.lite-code/config.json` 的 `mcp_servers` 段，也可以直接在**设置弹窗的 MCP 区块**可视化编辑（无需手改 JSON）：

```json
{
  "mcp_servers": {
    "sqlite": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sqlite", "./data.db"],
      "enabled": true
    }
  }
}
```

MCP 设置界面提供：服务器卡片（名称/命令/参数/启用开关/删除）、连接状态徽标（已连接 · N 工具 / 连接失败 + 错误详情）。保存调用 `POST /api/mcp`：落盘配置 → 断开全部连接 → 按新配置重连（任务运行中返回 409，避免运行中任务的工具集热变）。新工具从**下一个任务**开始生效——每个任务的 ToolRegistry 在装配时从 `MCPManager` 快照注册。

Core 启动时完成 MCP 初始化握手并调用 `tools/list`，外部工具以 `mcp_<server>_<tool>` 名称加入 ToolRegistry；调用时通过 `tools/call` 转发。MCP 工具与内置工具共用 Agent 的工具裁剪、安全审查、超时和事件流，不绕过现有安全边界。Core 关闭时终止 MCP Server 子进程。

**项目指令文件与 Skills**：最终项目按第 10 课的设计实现了完整机制——Core 启动任务时读取 workspace 根目录的 `AGENTS.md` / `Claude.md` / `CLAUDE.md` 注入 System Prompt；Skills 索引（名称 + 描述）常驻 System Prompt，`load_skill` 工具按需加载 `SKILL.md` 全文。两者都拼接在 System Prompt 末尾，保持缓存前缀稳定。

**工具结果的 Diff 展示**：编辑工具（第 9 课）成功回执现在是 `[Patch Success]: 已更新 <path> (+N -M)` 摘要 + Unified Diff 正文。前端用 `UnifiedDiff` 组件做 opencode 风格的**行级 diff 渲染**——在一个文件视图里同时显示插入（绿底）和删除（红底）的行，带新旧行号、hunk 分隔条：

```tsx
// UnifiedDiff.tsx —— 解析 unified diff → 行级交错视图
export function parseUnifiedDiff(diff: string): UnifiedDiffLine[] {
  const out: UnifiedDiffLine[] = [];
  // 正则解析每行类型：@@ 头 / + 插入 / - 删除 / 上下文
  // 跟踪 oldLine / newLine 行号
  ...
}

export default function UnifiedDiff({ diff }: { diff: string }) {
  return (
    <table className="unified-diff-table">
      {lines.map((l) => (
        <tr className={`udiff-row udiff-${l.type}`}>
          <td className="udiff-oldnum">{l.oldLine ?? ""}</td>
          <td className="udiff-newnum">{l.newLine ?? ""}</td>
          <td className="udiff-content">
            <span className="udiff-prefix">{l.type === "add" ? "+" : l.type === "del" ? "-" : " "}</span>
            {l.text.slice(1)}
          </td>
        </tr>
      ))}
    </table>
  );
}
```

`UnifiedDiff` 组件供**两处**复用：对话流工具卡片（`DiffPre` 通过 `UnifiedDiff` 渲染 tool result 的 diff 正文）和**文件查看器**（`FileViewer` 在双击文件 Tab 时直接显示 `UnifiedDiff`）。配套 CSS（`.udiff-row.udiff-add` 绿底、`.udiff-row.udiff-del` 红底、`.udiff-oldnum`/`.udiff-newnum` 双栏行号）让每次改文件的结果一目了然。

**文件查看器 FileViewer**：目录树文件双击 → 打开文件 Tab（`openFileTab`），该 Tab 不放对话。`FileViewer` 组件根据后端 `/api/fs/read` 返回的数据决定展示模式——有 git diff 时直接显示 `UnifiedDiff` 行级视图（只显示修改部分，不显示全量文件），无 diff 时显示普通文件内容（带行号的代码渲染）。顶部头部展示文件路径、语言徽标、行数、`+N −M` 增删徽标：

工具卡片与回复气泡使用同一套宽度约束：`.tool-cards` 沿用 `.msg-row` 的居中 padding（`max(24px, calc((100% - 820px) / 2))`），`.tool-card` 设 `max-width: 76%` 与 `.bubble` 一致，使工具调用与回答/思考气泡同宽、同样左对齐。

#### 3.6 上下文可观测性：`context:stats` 数据链路

「上下文情况」面板的数据不是前端瞎猜的，而是 AgentLoop 每轮把真实统计通过事件总线推出来的（见第 18 课的 `_emit_context_stats`）。完整链路：

```
AgentLoop._emit_context_stats()
   │  kernel.events.emit("context:stats", {...})
   ▼
TypedEventBus → TaskHandle 订阅 → asyncio.Queue
   ▼
SSE /api/tasks/{id}/events  →  data: {"type":"context:stats", ...}
   ▼
前端 ToolPanel 接收 → 更新「上下文情况」面板
```

后端侧（第 18 课已实现）每轮推送：模型名、上下文窗口大小、本轮 `prompt_tokens`、累计 `cache_hit_tokens / cache_miss_tokens / cache_hit_rate`、压缩次数、压缩节省 Token、窗口占用比例 `usage_ratio`，以及任务内的**工具调用次数、安全拦截次数、缓存感知的预估成本**（定价来自 models.dev per-model 数据，见第 4 课 §5 与第 17 课）。`TaskHandle` 在转发前还会把任务内统计**按差分口径**合并进会话级累计（`session` 段）——task 段是任务内累计全量、每轮重发，直接累加会重复入账，必须减去上次快照；任务结束时快照清零，下个任务从 0 重新起算。因此面板同时展示"本次调用"与"会话累计"两个视角，两段各有工具调用、安全拦截与预估成本。

前端 `App.tsx` 的 SSE 处理只需加一个 case：

```typescript
case "context:stats":     // 上下文情况面板（ToolPanel）
  setContextStats(ev.data);
```

`ToolPanel.tsx` 的「上下文情况」面板渲染（关键指标 + 进度条）：

```tsx
// 上下文情况面板（关键部分，位于右侧面板「上下文」Tab）
const ratio = stats?.task?.usage_ratio ?? 0;
const danger = ratio >= 0.9;   // 达到模型窗口 90%，自动压缩已触发
<div className="ctx-panel">
  <div className="ctx-progress-track" className={danger ? "danger" : ""}>
    <div className="ctx-progress-fill" style={{ width: `${Math.min(100, ratio * 100)}%` }} />
  </div>
  <div className="ctx-row"><span>窗口占用</span>
    <b>{Math.round(ratio * 100)}%</b></div>
  <div className="ctx-row"><span>缓存命中率</span>
    <b>{stats?.task?.cache_hit_rate != null ? `${Math.round(stats.task.cache_hit_rate * 100)}%` : "—"}</b></div>
  <div className="ctx-row"><span>输入 Token（本次）</span>
    <b>{stats?.task?.prompt_tokens?.toLocaleString() ?? 0}</b></div>
  <div className="ctx-row"><span>压缩次数 / 节省</span>
    <b>{stats?.task?.compression_count ?? 0} 次 / {(stats?.task?.compressed_tokens ?? 0).toLocaleString()}</b></div>
  {/* 统计并入了本面板：两段（本次/会话累计）各有以下三行 */}
  <div className="ctx-row"><span>工具调用</span>
    <b>{stats?.task?.tool_calls ?? 0} 次</b></div>
  <div className="ctx-row"><span>安全拦截</span>
    <b>{stats?.task?.blocked ?? 0} 次</b></div>
  <div className="ctx-row"><span>预估成本</span>
    <b>{stats?.task?.cost_estimate != null ? `$${stats.task.cost_estimate.toFixed(4)}` : "—"}</b></div>
</div>
```

**为什么值得做**：上下文压缩是"黑盒操作"——模型突然失忆时，如果面板显示"压缩 5 次、省了 30K token"，你能立刻定位到是 LLM 摘要压缩还是 Stage 1/Stage 2 裁剪导致的；缓存命中率骤降则说明 System Prompt 或工具定义在漂移、缓存断点被破坏了。**可观测性让"上下文管理"从玄学变成可排查的工程指标**。同时，窗口占用比例到达 **90%**（`usage_ratio >= 0.9`）时进度条变红，提示 AgentLoop 已在按 `max(预算下限, 90% × 模型窗口)` 自动压缩。

**切换会话时回填累计**：SSE 只在任务运行期间推送，切回历史会话时面板需要恢复累计数据。前端在切换会话时调用 `GET /api/context/stats?session_id=...`（后端 `app.get_context_session_stats` 聚合落盘统计），把 `session` 段回填到面板：

```typescript
// App.tsx — 切换会话
const ctx = await api.contextStats(id);
if (ctx.session && Object.keys(ctx.session).length > 0) {
  setContextStats({ model: "", context_window: 0, session: ctx.session });
}
```

#### 3.7 多 Tab 工作台（对话 / 文件 Tab 系统）

最终的前端是一个**多 Tab 工作台**：Tab 可以是对话或文件，彼此独立切换和关闭；对话状态按会话隔离，历史会话按当前工作区展示。

**为什么需要多 Tab？** 对话和文件查看通常需要并行进行。Tab 只负责当前工作台中的打开项，持久化历史则由后端 `SessionStore` 负责，两者不混为一层。

**Tab 数据结构**（`types.ts`）：

```typescript
interface TabItem {
  id: string;
  kind: "chat" | "file";     // 对话 Tab 或文件 Tab
  title: string;
  sessionId?: string;         // chat Tab 关联的会话 ID
  filePath?: string;          // file Tab 关联的文件路径
  fileContent?: string;       // 文件内容（缓存）
  fileDiff?: string;          // git diff（缓存）
  fileLanguage?: string;      // 编程语言
}
```

**多会话状态隔离**：每个已绑定会话的 `chat` Tab 状态（messages、streaming、running、stats 等）保存在 `chatStates: Record<sessionId, ChatSessionState>` 中。新建但尚未发送消息的 Tab 没有 `sessionId`，只存在于前端，不会写入后端。

```typescript
// App.tsx — 已绑定会话的状态按 sessionId 保存
const patchChat = (sessionId: string, patch: Partial<ChatSessionState>) => {
  setChatStates(prev => ({
    ...prev,
    [sessionId]: { ...(prev[sessionId] ?? EMPTY_CHAT), ...patch },
  }));
};
```

**窗口、项目与 Tab 的边界**：项目不是 Tab 的替代概念。一个窗口是工作台，内部可同时包含多个会话 Tab 和文件 Tab。当前窗口的“打开项目”通过当前 Core 热切换 workspace，不创建新的 Core；“新窗口打开项目”才会创建新的窗口和 Core。所有本地 Core 读取同一份 `~/.lite-code` 模型、安全和 Agent 配置。

**workspace 切换约束**：`AgentApp.workspace` 和工具实例依赖当前工作目录，因此当前窗口切换项目时必须先确认没有运行中的任务。没有任务时，当前 Core 更新 workspace，前端清空当前工作副本并重新请求新项目的历史会话；有任务时接口返回冲突，避免任务在执行过程中改变文件根目录。新窗口拥有自己的 Core 和任务流，但复用同一份用户配置。

**TabBar 组件**：水平 Tab 栏，左侧显示 Tab 列表（图标 + 标题），每个 Tab 可关闭（✕），至少保留一个 Tab。点击 Tab 只切换 `activeTabId`，不关闭其他会话的 SSE 连接；每个会话的任务 ID、流式缓冲和事件连接独立保存。

**关键坑：`.drag-region` 覆盖层**：Electron 无边框窗口的拖拽区（`position: fixed; height: 38px; z-index: 100`）正好盖在 `.main` 顶部的 TabBar 上，导致所有点击被拦截。解决：`.main` 加 `padding-top: 38px`，让 TabBar 从拖拽区下方开始。

**文件 Tab 的打开**：`Sidebar` 的目录树文件项增加 `onDoubleClick` 事件 → `App.tsx` 的 `openFileTab(filePath)` → 调用 `/api/fs/read` 读取文件内容 + git diff → 创建 `kind: "file"` 的 Tab（不显示 Composer / ToolPanel，只显示 FileViewer）。`FileViewer` 有 diff 时展示 `UnifiedDiff`（仅修改部分），无 diff 时展示普通文件内容（带行号）。

**打开项目与 Tab**：在本地桌面模式下，“打开项目”调用当前 Core 的 workspace 切换接口。切换成功后窗口重新加载前端，当前 Tab 和工作副本清空，只保留一个占位会话 Tab；侧边栏显示新 workspace 的历史会话。配置目录仍固定在用户目录，项目切换不会创建新的内核，也不会丢失模型配置。

**会话生命周期**：点击「新建会话」或切换项目只创建占位 Tab。用户发送第一条消息时，前端才调用 `POST /api/sessions`，将返回的 ID 绑定到当前 Tab，再调用 `POST /api/chat`。没有用户消息的 session 不进入历史列表；列表接口只过滤，不删除正在初始化的 session。会话标题默认取第一条用户消息，显式设置的 `metadata.name` 优先。

#### 4. 多 LLM 配置界面 (`SettingsModal.tsx`)

支持多个预置供应商和任意数量的自定义供应商实例：

| 供应商 | ID | 类型 | 默认端点 |
|---|---|---|---|
| DeepSeek | `deepseek` | OpenAI 兼容 | `https://api.deepseek.com` |
| OpenAI | `openai` | OpenAI 兼容 | `https://api.openai.com/v1` |
| Kimi (Moonshot) | `kimi` | OpenAI 兼容 | `https://api.moonshot.cn/v1` |
| 通义千问 | `qwen` | OpenAI 兼容 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 GLM | `glm` | OpenAI 兼容 | `https://open.bigmodel.cn/api/paas/v4` |
| Anthropic Claude | `anthropic` | Anthropic 原生 | `https://api.anthropic.com/v1` |
| 自定义实例 | `custom_*` | OpenAI 兼容 | 用户输入 |

配置界面功能：
- 供应商选择器（网格按钮，标记已配置/未配置）
- API Key 密码输入（脱敏显示）
- Base URL 编辑
- 模型下拉选择 + 自定义输入（`<datalist>`）
- 新增多个自定义供应商，每个实例独立保存名称、Key、URL 和模型列表
- Temperature 滑块
- **自定义 Header 多行编辑**（支持 `Key: Value` 或 `Key=Value`，可配置多个请求头）
- **上下文长度 tokens 输入**（留空自动：models.dev 同步 / 内置表兜底）
- 测试连接按钮（真实 API 调用）
- 保存配置按钮

配置持久化在 `.lite-code/config.json` 的 `"llm"` 段。设置弹窗内部维护编辑态，点击保存后由后端写盘并重建适配器；只有保存成功后新的配置才用于后续任务。Header 文本在测试和保存时都会即时解析，因此用户无需先让输入框失焦；清空 Header 文本并保存会删除此前保存的自定义 Header。

**当前会话的模型切换**：全局配置中的 active 供应商和模型仍然是默认值。聊天 Tab 可以在输入区选择已经配置的供应商和模型，选择只保存到当前会话；其他会话、文件 Tab 和全局默认配置不受影响。没有单独选择模型的会话始终跟随系统默认，选择“系统默认”则清除当前会话的覆盖。正在运行的任务保持原模型，新的模型选择从下一条消息开始生效。

**设置弹窗的编辑态与关闭策略**：配置表单通常包含 API 请求、密码字段和多个动态模型条目，不能把异步请求的完成回调直接当成弹窗关闭信号。保存成功时应保留弹窗并显示结果，让用户继续检查或切换供应商；保存失败时也应保留当前编辑态并显示错误，避免用户重新输入配置。遮罩层适合阻止背景交互，不应默认承担关闭弹窗的行为；关闭应由明确的关闭按钮触发。这样可以避免 React 组件卸载导致未保存编辑丢失，也能让异步保存的状态反馈始终在当前上下文中可见。

```tsx
// SettingsModal：保存反馈属于弹窗内部状态
const [saveResult, setSaveResult] = useState<string | null>(null);

async function save() {
  try {
    await api.updateLLMConfig(activeProvider, providers);
    setSaveResult("配置已保存");
    onSaved(); // 只通知父组件刷新，不负责关闭弹窗
  } catch (error) {
    setSaveResult(`保存失败: ${String(error)}`);
  }
}

return <div className="modal-overlay">
  <div className="modal">{/* 只有明确的关闭按钮调用 onClose */}</div>
</div>;
```

**上下文长度手动覆盖**：每个供应商可手填 `context_window`，留空则自动解析（`LLMRegistry.get_context_window` 四级优先级：① 手动覆盖 → ② models.dev → ③ 内置表 → ④ 128K 默认，见第 17 课 §5）。另有全局配置 `context_full_turns`（默认 2）控制策略 B 裁剪时保留的最近完整轮数——即第 3 课的 `keep_recent_full_turns`，在 `.lite-code/config.json` 的根级配置即可调。

#### 5. Web 侧稳定性加固

真实网络环境下，前端会遇到各种异常：后端无响应、LLM 超时、网络断连、渲染崩溃。Web UI 针对这些场景做了系统性加固。

**React ErrorBoundary**（`web/src/components/ErrorBoundary.tsx`）

React 渲染异常会导致整个应用白屏。用 ErrorBoundary 包裹根组件，捕获异常后显示错误页 + 重载按钮：

```tsx
class ErrorBoundary extends React.Component<Props, State> {
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, message: error.message };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="crash-screen">
          <h2>界面渲染出错</h2>
          <p className="crash-message">{this.state.message}</p>
          <button onClick={() => window.location.reload()}>🔄 重新加载</button>
        </div>
      );
    }
    return this.props.children;
  }
}
```

**API 请求超时**（`web/src/api.ts`）

所有 fetch 请求加 `AbortController` + 15s 超时，防止后端无响应时前端永久挂起：

```typescript
const TIMEOUT = 15000;
async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  try {
    const res = await fetch(url, { signal: controller.signal, ...init });
    ...
  } finally { clearTimeout(timer); }
}
```

**任务卡死检测**（`App.tsx`）

LLM 调用可能超时或网络中断。传输层的 read timeout 只表示一段时间没有收到字节；如果服务端持续发送零碎数据，它不会限制整轮请求的总时长。因此 AgentLoop 还需要独立的业务硬超时（例如 `llm_timeout = 180s`）：

```python
content, tool_calls, usage = await asyncio.wait_for(
    adapter.chat_stream(messages, tools, events),
    timeout=llm_timeout,
)
```

硬超时触发后应写入明确的错误消息，并发送 `task:error` 或 `task:done`，确保前端不会永久保持运行状态。前端还可以跟踪 SSE 最后事件时间戳，超过 45s 无事件时显示黄色警告横幅和停止按钮：

```typescript
const lastEventTimeRef = useRef<number>(Date.now());
const [stalled, setStalled] = useState(false);

// SSE 事件到来时更新：lastEventTimeRef.current = Date.now();
// 定时器每 15s 检查
useEffect(() => {
  if (!running) { setStalled(false); return; }
  const timer = setInterval(() => {
    if (Date.now() - lastEventTimeRef.current > 45000) {
      setStalled(true);
    }
  }, 15000);
  return () => clearInterval(timer);
}, [running]);
```

**调试日志面板**（`App.tsx`）

右下角「日志」按钮，点击展开可滚动日志面板，记录所有 SSE 事件（提交、连接、工具调用、完成/错误等），方便排查卡死原因：

```typescript
const [debugLogs, setDebugLogs] = useState<string[]>([]);
const pushLog = useCallback((msg: string) => {
  setDebugLogs((prev) => [...prev.slice(-200), `${new Date().toLocaleTimeString()} ${msg}`]);
}, []);
```

**SSE 断连自动重连**

前端 `EventSource` 的 `onerror` 不主动 close，让浏览器自动重连（`EventSource` 内置自动重连机制）。服务端对应的 `_stream()` 生成器在客户端断开时不清理任务，确保重连后能继续读取事件：

```python
async def _stream():
    try:
        while True:
            item = await asyncio.wait_for(handle.queue.get(), timeout=15)
            ...
    except asyncio.CancelledError:
        # 客户端断连：不清理任务，支持重连继续读取
        raise
    finally:
        # 仅在任务真正结束时（已收到 [DONE]）清理
        if handle.done and handle.queue.empty():
            tasks.cleanup(task_id)
```

### 本课小结

在本课中，我们为 `lite-code` 构建了完整的 Web 外壳：

1. **FastAPI 服务层**：REST + SSE 双通道、路径越界防护、"先打开项目"的显式状态、SSE 背压与终止事件优先级；
2. **React Web UI**：ref 缓冲区解决高频流式渲染、有序工作项时间线、真实目录树 + git 状态徽标、多 Tab 工作台；
3. **上下文可观测性**：`context:stats` 从 AgentLoop 到面板的完整数据链路，让 Token 治理可排查；
4. **多 LLM 配置**：七类供应商 + 自定义实例、会话级模型覆盖、编辑态保护；
5. **稳定性加固**：请求超时、卡死检测、ErrorBoundary、SSE 自动重连。

至此 `lite-code` 已经可以通过 `python -m litecode serve` 在浏览器中使用。下一课我们将开启 **第21课：Electron 桌面应用** —— 用 Electron 包裹 Web UI，实现多窗口项目工作区、真实终端和一键打包发布！

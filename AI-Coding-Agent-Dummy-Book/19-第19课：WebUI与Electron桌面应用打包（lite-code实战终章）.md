在前面的课程中，我们从架构设计、核心 Kernel、插件体系、AgentLoop 状态机到沙箱防护，一步步完成了 `lite-code` 框架的底层构建。

本课作为手写实战的**收官之作**，我们将为 `lite-code` 赋予成熟的生产级外壳：

1. **FastAPI 服务层**：REST API + SSE 流式推送，连接前后端；
2. **React 现代化 Web UI**：流式 Markdown、工具调用卡片、审批弹窗、会话/文件树/成本面板；
3. **Electron 桌面外壳**：自动拉起 Python Core、窗口管理、"打开项目"切换工作区；
4. **多 LLM 配置界面**：DeepSeek / OpenAI / Anthropic / 通义千问等多供应商切换；
5. **三种运行形态**：本地桌面应用 / 远程 Core / 纯浏览器访问；
6. **一键打包发布**：PyInstaller 后端 + electron-builder 出 .app/.dmg。

#### 1. 架构总览

```text
+------------------------------------------------------------+
|                    Electron 桌面外壳                        |
|  +------------------------------------------------------+  |
|  |  React Web UI (sidecar 浏览器窗口)                    |  |
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
1. **本地桌面**：`npm start` 或 `npm run dev`，Electron 自动 spawn Python Core
2. **远程 Core**：`~/.lite-code/client.json` 配置 `coreUrl`，窗口直连远程服务器
3. **纯浏览器**：`python -m litecode serve` 后访问 `http://127.0.0.1:8787`

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
@app.get("/api/workspace/tree")  # 文件树
```

**SSE 流式推送**：每个任务创建独立的 `asyncio.Queue`，AgentLoop 运行时通过 `TypedEventBus` 广播事件（`llm:stream`、`tool:before_execute`、`approval:request` 等），`TaskRunner` 订阅事件并推送到队列，SSE 端点从队列消费：

```python
async def _stream():
    while True:
        item = await asyncio.wait_for(handle.queue.get(), timeout=15)
        yield f"data: {json.dumps(item)}\n\n"

return StreamingResponse(_stream(), media_type="text/event-stream")
```

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
    ├── ChatView.tsx   # 聊天区：流式 Markdown + 工具卡片 + 审批
    ├── Composer.tsx   # 输入框
    ├── Sidebar.tsx    # 侧边栏：会话/文件/统计/打开项目
    └── SettingsModal.tsx  # LLM 设置弹窗
```

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

#### 3.5 OpenCode 风格交互优化

在完成基础功能后，我们参考 OpenCode 的交互设计，对 UI 做了几项关键优化：

**右侧工具面板**：工具调用卡片不再混在对话流中，而是被移到右侧独立面板（`ToolPanel.tsx`）。每个工具调用显示为紧凑单行（图标 + 名称 + 结果摘要），点击展开查看参数与结果。面板自动滚动到底部，条目过多时通过 `flex-shrink: 0` + `overflow-y: auto` 保证滚动而非压缩：

```text
┌─────── 对话区 ───────┐  ┌── 工具面板 ────┐
│ 用户：帮我查启动原因    │  │ 列表: file_tree ▸│
│                       │  │ 列表: git_status ▸│
│ 思考过程 ▸             │  │ 列表: read_file ▸│
│ (可折叠推理)          │  │ 列表: search_code▸│
│                       │  │ 列表: read_file ▸│
│ ⚡ 分析完成。          │  │ 列表: list_dir  ▸│
│ 以下是完整报告…         │  │ 列表: read_file ▸│
│                       │  └──────────────────┘
└───────────────────────┘
```

**思考与回答分离**：历史上，每次模型调用工具的中间推理（`assistant` 消息同时携带 `content` 和 `tool_calls`）都会被渲染为独立气泡，导致对话区呈现"多个推理气泡 + 空气泡"的混乱状态。`buildTurns` 函数将整个用户任务下的所有中间推理文本合并为一个 **thinking 块**（`<details>` 折叠），最终回答才作为正常气泡显示：

```javascript
function buildTurns(messages) {
  // 每个 user 消息对应一个 thinking 块
  // 所有 assistant+tool_calls 的 content 累积到 thinking
  // 最终回答（assistant 无 tool_calls）作为独立气泡
}
```

**Tab 快捷键切换 Agent**：全局 `keydown` 监听，按 Tab 在 Build/Plan 间循环切换，`preventDefault` 阻止焦点跳转——输入框始终聚焦，可连续打字 + 切 Agent + 发送。

**停止按钮合一**：任务运行时，发送按钮（➤）变为红色停止按钮（■），点击即停止。不再有独立的悬浮停止条，避免遮挡。

**目录树打开项目**：浏览器模式下，点击"打开项目"弹出 `ProjectPicker` 目录树选择器，通过 `/api/fs/list` 后端接口逐层浏览文件系统，选完后自动切换工作区并跳转到文件 tab。

**单换行与表格美化**：通过 `remark-breaks` 插件让 Markdown 单换行（`\n`）渲染为 `<br>`，步骤性输出自然分行。表格加 `display: block + overflow-x: auto`，超宽表格横向滚动。

**Session 首句标题**：会话标题不再显示 `session_xxx`，而是取首条用户消息的前 40 字（由 `_session_title` 在后端完成），更直观。

**Build/Plan 前端切换**：`Composer.tsx` 加入 Agent 选择栏，显示 Build / Plan 两个按钮，当前选中的高亮。右侧显示 `Tab` 小标签提示快捷键。

**后端新端点**：配套新增 `/api/agents` 返回 Agent 列表、`/api/workspace` 运行时切换工作区、`/api/fs/list` 浏览任意目录。

#### 3.6 上下文可观测性：`context:stats` 数据链路

「上下文情况」面板的数据不是前端瞎猜的，而是 AgentLoop 每轮把真实统计通过事件总线推出来的（见第 17 课的 `_emit_context_stats`）。完整链路：

```
AgentLoop._emit_context_stats()
   │  kernel.events.emit("context:stats", {...})
   ▼
TypedEventBus → TaskRunner 订阅 → asyncio.Queue
   ▼
SSE /api/tasks/{id}/events  →  data: {"type":"context:stats", ...}
   ▼
前端 ToolPanel 接收 → 更新「上下文情况」页签
```

后端侧（第 17 课已实现）每轮推送：模型名、上下文窗口大小、本轮 `prompt_tokens`、累计 `cache_hit_tokens / cache_miss_tokens / cache_hit_rate`、压缩次数、压缩节省 Token、窗口占用比例 `usage_ratio`。TaskRunner 在转发前还会把任务内统计累加进会话级累计（`session` 段），因此面板同时展示"本次调用"与"会话累计"两个视角。

前端 `App.tsx` 的 SSE 处理只需加一个 case：

```typescript
case "context:stats":     // 上下文情况面板（ToolPanel）
  setContextStats(ev.data);
```

`ToolPanel.tsx` 的「上下文情况」页签渲染（关键指标 + 进度条）：

```tsx
// 上下文情况面板（关键部分，位于右侧面板第一个页签）
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
</div>
```

**为什么值得做**：上下文压缩是"黑盒操作"——模型突然失忆时，如果面板显示"压缩 5 次、省了 30K token"，你能立刻定位到是 Stage 1 压缩工具细节还是 Stage 2 整轮删除导致的；缓存命中率骤降则说明 System Prompt 或工具定义在漂移、缓存断点被破坏了。**可观测性让"上下文管理"从玄学变成可排查的工程指标**。同时，窗口占用比例到达 **90%**（`usage_ratio >= 0.9`）时进度条变红，提示 AgentLoop 已在按 `min(预算, 90% × 模型窗口)` 自动压缩。

#### 4. 多 LLM 配置界面 (`SettingsModal.tsx`)

支持 7 个预置供应商 + 自定义：

| 供应商 | ID | 类型 | 默认端点 |
|---|---|---|---|
| DeepSeek | `deepseek` | OpenAI 兼容 | `https://api.deepseek.com` |
| OpenAI | `openai` | OpenAI 兼容 | `https://api.openai.com/v1` |
| Kimi (Moonshot) | `kimi` | OpenAI 兼容 | `https://api.moonshot.cn/v1` |
| 通义千问 | `qwen` | OpenAI 兼容 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 GLM | `glm` | OpenAI 兼容 | `https://open.bigmodel.cn/api/paas/v4` |
| Anthropic Claude | `anthropic` | Anthropic 原生 | `https://api.anthropic.com/v1` |
| 自定义 | `custom` | OpenAI 兼容 | 用户输入 |

配置界面功能：
- 供应商选择器（网格按钮，标记已配置/未配置）
- API Key 密码输入（脱敏显示）
- Base URL 编辑
- 模型下拉选择 + 自定义输入（`<datalist>`）
- Temperature 滑块
- 测试连接按钮（真实 API 调用）
- 保存配置按钮

配置持久化在 `.lite-code/config.json` 的 `"llm"` 段，切换供应商即时生效（`AgentApp.close_adapter()` 重建适配器）。

#### 5. Electron 桌面外壳 (`electron/main.js`)

Electron 主进程负责三种形态的启动管理：

```javascript
// 启动逻辑
if (process.env.LITECODE_DEV_URL) {
  // 形态1：开发模式（Vite + Python Core 由 concurrently 管理）
  createWindow(process.env.LITECODE_DEV_URL);
} else if (config.coreUrl) {
  // 形态2：远程 Core
  injectRemoteToken(config.token);
  createWindow(config.coreUrl);
} else {
  // 形态3：本地桌面 —— spawn Python Core
  const { url } = await spawnLocalCore();
  createWindow(url);
}
```

**关键特性**：
- `titleBarStyle: "hiddenInset"` 无边框窗口 + 红绿灯避开侧边栏
- `sandbox: true` + `contextIsolation: true` + `preload.js` 最小化安全桥
- `dialog.showOpenDialog` + **热切换工作区**：选择目录后调用后端 `POST /api/workspace` 让当前进程切换，不重启后端（毫秒级）；失败才回退重启进程
- 60s 后端启动超时兜底，`will-quit` 时 SIGTERM 回收后端进程
- 远程模式支持 Bearer Token 注入（`session.webRequest.onBeforeSendHeaders`）

**窗口配置**：
```javascript
new BrowserWindow({
  width: 1280, height: 820, minWidth: 960, minHeight: 640,
  backgroundColor: "#0d1117",
  titleBarStyle: "hiddenInset",
  trafficLightPosition: { x: 18, y: 18 },
  webPreferences: { contextIsolation: true, sandbox: true, ... }
})
```

#### 6. 稳定性加固

桌面应用在真实环境中会遇到各种异常：后端启动慢、LLM 超时、网络断连、渲染进程崩溃。`lite-code` 针对这些场景做了系统性加固。

**Electron 启动加载页**（`electron/loading.html`）

PyInstaller 打包的后端二进制首次解压需要 10-30s，如果等后端就绪再创建窗口，用户会看到一片空白。解决：窗口**立即创建**，显示内置加载页，后端就绪后自动跳转主界面。

```html
<!-- electron/loading.html（核心结构） -->
<body>
  <div class="logo">⚡</div>
  <div class="title">lite-code</div>
  <div class="spinner"></div>
  <div class="progress"><div class="progress-fill" id="fill"></div></div>
  <div class="status" id="status">正在启动内核…</div>
  <div class="error" id="errorBox">后端启动失败<button onclick="location.reload()">重试</button></div>
  <script>
    // 步骤动画，每 4s 推进一格
    const steps = ["正在启动内核…", "加载安全策略…", "准备代码工具…", "连接模型服务…", "即将就绪…"];
    setInterval(() => {
      step = Math.min(step + 1, steps.length - 1);
      document.getElementById("status").textContent = steps[step];
      document.getElementById("fill").style.width = (step / (steps.length - 1)) * 100 + "%";
    }, 4000);
  </script>
</body>
```

主进程启动逻辑改为先创建窗口再 await 后端：

```javascript
// 立即创建窗口 + 加载页（即使后端未就绪）
const loadingUrl = `file://${path.join(__dirname, "loading.html")}`;
createWindow(loadingUrl);

const { url } = await spawnLocalCore();    // 后端可能耗时 30s
if (mainWindow && !mainWindow.isDestroyed()) {
  mainWindow.loadURL(url);                 // 就绪后跳转主界面
}
```

**渲染进程崩溃自动恢复**（`electron/main.js`）

Electron 渲染进程可能因各种原因崩溃（白屏）。监听 `render-process-gone` 事件，1s 后自动 reload；页面加载失败时最多重试 3 次：

```javascript
mainWindow.webContents.on("render-process-gone", (event, details) => {
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload();
  }, 1000);
});

let failCount = 0;
mainWindow.webContents.on("did-fail-load", (event, code, desc) => {
  if (++failCount <= 3) {
    setTimeout(() => mainWindow?.reload(), 2000);
  }
});
mainWindow.webContents.on("did-finish-load", () => { failCount = 0; });
```

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

LLM 调用可能超时（120s）或网络中断。跟踪 SSE 最后事件时间戳，超过 45s 无响应时显示黄色警告横幅 + 停止按钮：

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

#### 7. 一键打包 (`npm run package`)

```bash
npm run build:web                    # 构建 React 前端 → web/dist/
node scripts/package-backend.mjs     # PyInstaller → release/backend/lite-code-backend(.exe)
npx electron-builder --win nsis      # Windows 安装包（macOS 用 --mac）
```

Windows 一键打包脚本 `scripts/build-windows.ps1` 把上述步骤串起来：创建 venv → 安装 Python 依赖 → 安装前端依赖 → 构建前端 → PyInstaller 后端 → NSIS 安装包。

**产物**（以 Windows 为例）：
```
release/
├── lite-code Setup <版本号>.exe    (约 116MB，NSIS 安装包，可改安装目录)
├── lite-code Setup <版本号>.exe.blockmap
└── win-unpacked/lite-code.exe       (解包目录，可直接运行)
```

关键工程点：
- **后端进包**：`package.json` 的 `extraResources` 把 `release/backend` 目录（含 `lite-code-backend.exe`）复制到安装后的 `resources/litecode-bin/`，Electron 主进程通过 `resolvePython()` 优先使用内置二进制；
- **图标**：`scripts/app-icon.svg` 直接配置为 `win.icon`，electron-builder 自动栅格化为 ICO；
- **加载页**：PyInstaller 单文件后端首次解压需 10-30s，主进程先显示 `loading.html`，后端就绪后跳转主界面。

**开发模式**：`npm run dev`（concurrently 编排 Python Core + Vite + Electron，一行命令三步启动）

#### 8. 全课程总结与回顾

恭喜你！到这里我们已经完成了全套 **19 课** 的 Code Agent 深度课程，并手写出了完整的 `lite-code` 框架。

```text
+-----------------------------------------------------------------------+
|                     lite-code 全景架构                                  |
+-----------------------------------------------------------------------+
|  [模块一] ReAct 与循环 (第1-5课)                                       |
|  - LLM 流式 Tool Calling 解析 | JSON 自愈 / 死循环检测 | 输出截断      |
|  - Token 预算与策略B滑动裁剪 | 动态 System Prompt | Prompt 缓存       |
|  - Token 节省策略（多层预算 / 带外存储 / 缓存铁律）                     |
+-----------------------------------------------------------------------+
|  [模块二] Code Agent 增强能力 (第6-10课)                               |
|  - Ripgrep 代码感知 | Tree-sitter AST 分析 | 沙箱隔离                 |
|  - Search-Replace / Unified Diff | MCP 外部工具生态接入               |
+-----------------------------------------------------------------------+
|  [模块三] 核心架构 (第11-14课)                                         |
|  - Cordis 微内核与时空解耦 | 洋葱模型中间件 / 生命周期钩子            |
|  - 子 Agent 编排 | Build/Plan 与用户自定义 Agent                       |
+-----------------------------------------------------------------------+
|  [模块四] 手写实战 lite-code (第15-19课)                               |
|  - Python Core | 多 LLM 供应商 | 全套工具集 | AgentLoop 主循环        |
|  - 动态黑白名单 | Web 审批 | 上下文可观测性 | React UI | Electron 打包 |
+-----------------------------------------------------------------------+
```

至此，你不仅掌握了 Agent Harness 框架的完整理论体系，还手握一个**可独立扩展、架构清晰、可直接运行的桌面应用**。你可以基于 `lite-code` 扩展更多专属插件（如 Git 自动化、代码审查、数据库查询、MCP 工具等），打造属于你自己的专用 AI 开发助手！

**启动方式**：
```bash
npm run dev      # 开发模式：Python Core(8787) + Vite(5173) + Electron 窗口
npm start        # 生产模式：构建前端 → 自动拉起 Python Core → 窗口
npm run package  # 打包：PyInstaller 后端 + electron-builder（Windows 出 NSIS 安装包）
```
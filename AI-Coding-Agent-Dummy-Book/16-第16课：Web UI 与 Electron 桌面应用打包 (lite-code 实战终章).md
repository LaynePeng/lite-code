在前面的十五课中，我们从架构设计、核心 Kernel、插件体系、AgentLoop 状态机到沙箱防护，一步步完成了 `lite-code` 框架的底层构建。

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

app = FastAPI(title="lite-code", version="0.1.0")
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

#### 4. 多 LLM 配置界面 (`SettingsModal.tsx`)

支持 7 个预置供应商 + 自定义：

| 供应商 | ID | 类型 | 默认端点 |
|---|---|---|---|
| DeepSeek | `deepseek` | OpenAI 兼容 | `https://api.deepseek.com/v1` |
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
- `dialog.showOpenDialog` 实现"打开项目"切换工作区
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

#### 6. 一键打包 (`npm run package`)

```bash
npm run build:web    # 构建 React 前端 → web/dist/
node scripts/generate-icon.mjs   # 生成应用图标 → release/resources/app-icon.icns
node scripts/package-backend.mjs # PyInstaller → release/backend/lite-code-backend
npx electron-builder --mac       # → release/lite-code-0.1.0-arm64.dmg
```

**产物**：
```
release/
├── lite-code-0.1.0-arm64.dmg         (109MB，双击安装)
├── lite-code-0.1.0-arm64-mac.zip     (106MB)
└── mac-arm64/lite-code.app           (已 ad-hoc 签名)
```

**开发模式**：`npm run dev`（concurrently 编排 Python Core + Vite + Electron，一行命令三步启动）

#### 7. 全课程总结与回顾

恭喜你！到这里我们已经完成了全套 **16 课** 的 Code Agent 深度课程，并手写出了完整的 `lite-code` 框架。

```text
+-----------------------------------------------------------------------+
|                     lite-code 工业级全景架构                            |
+-----------------------------------------------------------------------+
|  [Module 1] ReAct 与 Loop 状态机 (第1-4课)                            |
|  - AgentLoop 状态控制 | Prompt Stream 解析 | 结构化 Tool Execution    |
|  - JSON 自愈 / 死循环检测 / 输出截断 / Token 预算 / 动态 System Prompt |
+-----------------------------------------------------------------------+
|  [Module 2] 工业级 Code Agent 增强能力 (第4-7课)                       |
|  - Ripgrep 搜索 | Tree-sitter AST 分析 | 差异 Patch 增量替换           |
|  - 本地进程沙箱 + 环境变量擦除                                         |
+-----------------------------------------------------------------------+
|  [Module 3] 工具协议与开放生态 (第8课)                                 |
|  - 标准 Model Context Protocol (MCP) JSON-RPC 2.0 协议集成             |
+-----------------------------------------------------------------------+
|  [Module 4] 微内核与子 Agent 编排 (第9-11课)                           |
|  - Cordis 时空解耦 | Interceptor 洋葱模型拦截链 | Sub-Agent 派发编排    |
+-----------------------------------------------------------------------+
|  [Module 5] 手写实战 lite-code (第12-16课)                             |
|  - Python Core | 多 LLM 供应商 | 全套工具集 | AgentLoop                 |
|  - 动态黑白名单 | Web 审批 | React UI | Electron 桌面 | 打包发布       |
+-----------------------------------------------------------------------+
```

至此，你不仅掌握了 Agent Harness 框架的完整理论体系，还手握一个**可独立扩展、架构清晰、可直接运行的桌面应用**。你可以基于 `lite-code` 扩展更多专属插件（如 Git 自动化、代码审查、数据库查询、MCP 工具等），打造属于你自己的专用 AI 开发助手！

**启动方式**：
```bash
npm run dev      # 开发模式：Python Core(8787) + Vite(5173) + Electron 窗口
npm start        # 生产模式：构建前端 → 自动拉起 Python Core → 窗口
npm run package  # 打包：PyInstaller 后端 + electron-builder → .app/.dmg
```
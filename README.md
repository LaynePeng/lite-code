# lite-code

> 手写内核的 Code 开发 Agent 桌面工具。基于《AI Code Agent 手把手》16 课教程，从零搭建的工业级 Agent Harness。

**Python 内核 + FastAPI 服务 + React 前端 + Electron 桌面外壳，纯手写，不依赖 LangChain 等高层框架。**

## 架构

```text
Electron 桌面外壳
├── React Web UI（聊天/工具卡片/审批/会话/文件树/成本面板）
│   └── HTTP + SSE ↔ Python FastAPI 后端
└── 自动 spawn / 远程直连 / 纯浏览器三种运行形态

Python 后端（litecode/）
├── core/       内核（事件总线 / 洋葱中间件 / Kernel / AgentLoop / Token 预算 / 上下文裁剪）
├── llm/        LLM 多供应商适配器（DeepSeek / OpenAI / Anthropic / 通义千问 / 等 7 个）
├── tools/      17 个工具（文件 / 代码搜索 / AST / 精确编辑 / Shell / Git / 审查 / 子 Agent）
├── security/   安全沙箱（动态黑白名单 / 三级风险 / Web 审批卡）
├── server/     FastAPI 服务（REST + SSE 流式推送）
└── orchestration/  子 Agent 编排（角色裁剪工具集）
```

## 功能特性

- **17 个内置工具**：文件读写、Ripgrep 搜索、Tree-sitter AST 大纲、Search-Replace 精确编辑、Unified Diff、受限 Shell、Git 五件套、代码审查、子 Agent 编排
- **多 LLM 供应商**：DeepSeek / OpenAI / Kimi / 通义千问 / 智谱 GLM / Anthropic Claude / 自定义
- **安全防御**：三级风险模型（SAFE / MEDIUM / HIGH）+ 动态黑白名单热加载 + Web 审批卡
- **Agent 增强**：JSON 自愈、死循环检测、输出截断、Token 预算裁剪、动态 System Prompt
- **三种运行形态**：本地桌面（Electron + 自动 spawn 后端）/ 远程 Core / 纯浏览器
- **多会话管理**：创建/切换/恢复/删除会话，JSON 原子写盘持久化

## 快速开始

```bash
# 前提：Python 3.11+、Node 18+、DEEPSEEK_API_KEY 环境变量（或其他 LLM Key）

# 安装 Python 依赖
python3 -m venv .venv
.venv/bin/pip install -e .[dev]

# 安装前端 + Electron 依赖
npm install

# 一键启动（开发模式：Core + Vite + Electron 窗口）
npm run dev

# 或生产模式（构建前端后启动）
npm start
```

## 打包

```bash
npm run build:web      # 构建前端
node scripts/generate-icon.mjs   # 生成应用图标
node scripts/package-backend.mjs  # PyInstaller 后端二进制
npx electron-builder --mac        # → release/*.dmg
```

## 教程

完整的 16 课从零搭建教程在 `AI-Coding-Agent-Dummy-Book/` 目录，涵盖：
- 第 1-4 课：LLM 接口封装 / Agent 循环 / Token 预算 / 动态 System Prompt
- 第 5-8 课：Tree-sitter AST / 沙箱隔离 / 精确编辑 / MCP 协议
- 第 9-11 课：Cordis 微内核 / 中间件 / 子 Agent 编排
- 第 12-16 课：手写实战（Python Core → 多 LLM → AgentLoop → 安全 → Web UI + Electron）

## License

MIT
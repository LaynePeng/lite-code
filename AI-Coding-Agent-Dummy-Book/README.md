# 《AI Code Agent 手把手》— lite-code 从零实战

> 一套从零手写工业级 Code Agent 的完整教程。读完 16 课，你将亲手造出 [lite-code](https://github.com/LaynePeng/lite-code) —— 一个可运行的桌面 Code Agent 应用（Python 内核 + React UI + Electron 外壳）。

## 你将学到

- **不依赖 LangChain 等高层框架**，纯手写 Agent 内核
- LLM 流式 Tool Calling 底层解析、JSON 自愈、死循环检测、Token 预算
- 代码感知（Ripgrep / Tree-sitter AST）、精确编辑（Search-Replace / Diff）
- 沙箱隔离、动态黑白名单、Web 审批（Human-in-the-Loop）
- Cordis 微内核、洋葱中间件、子 Agent 编排
- 多 LLM 供应商接入、FastAPI 服务、React UI、Electron 桌面打包

## 课程目录

| 模块 | 课程 | 主题 |
|---|---|---|
| **模块一：ReAct 与循环** | [第 1 课](01-第1课：LLM 原生接口封装与 Tool Calling 底层流式解析.md) | LLM 原生接口封装 + Tool Calling 流式解析 |
| | [第 2 课](02-第2课：Agent 控制循环的异常容错、死循环中断与 Token 预算控制.md) | JSON 自愈 / 死循环检测 / 输出截断 |
| | [第 3 课](03-第3课：上下文管理、Token 动态计算与 Dynamic System Prompt.md) | Token 预算 / 滑动裁剪 / 动态 System Prompt |
| | [第 4 课](04-第4课：代码感知、 Ripgrep 高效搜索与代码库结构构建.md) | Ripgrep 搜索 / 文件树 |
| **模块二：Code Agent 增强** | [第 5 课](05-第5课：基于 Tree-sitter 的抽象语法树（AST）分析与代码依赖图构建.md) | Tree-sitter AST 分析 |
| | [第 6 课](06-第6课：沙箱隔离技术 (Execution Sandbox).md) | 沙箱隔离 |
| | [第 7 课](07-第7课：精确代码编辑与 Apply Patch 机制.md) | Search-Replace / Unified Diff |
| | [第 8 课](08-第8课：标准 Model Context Protocol (MCP) 接入.md) | MCP 协议接入 |
| **模块三：核心架构** | [第 9 课](09-第9课：Cordis 插件内核设计 (Spatiotemporal Composability).md) | Cordis 微内核 / 时空解耦 |
| | [第 10 课](10-第10课：依赖注入与插件生命周期管理.md) | 洋葱中间件 / 生命周期钩子 |
| | [第 11 课](11-第11课：多 Agent 协作与子 Agent 调度 (Sub-agent Orchestration).md) | 子 Agent 编排 |
| **模块四：手写实战** | [第 12 课](12-第12课：核心 Core 内核编码 (lite-code 实战第一篇).md) | Python Core 内核 |
| | [第 13 课](13-第13课：LLM 多供应商适配器与核心工具集 (lite-code 实战第二篇).md) | 多 LLM + 17 工具 |
| | [第 14 课](14-第14课：AgentLoop 主循环与增强集成 (lite-code 实战第三篇).md) | AgentLoop 主循环 |
| | [第 15 课](15-第15课：安全沙箱与高危拦截实战 (lite-code 实战第四篇).md) | 安全沙箱 + Web 审批 |
| | [第 16 课](16-第16课：Web UI 与 Electron 桌面应用打包 (lite-code 实战终章).md) | Web UI + Electron + 打包 |

## 学习路线

1. **理论入门（第 1-4 课）**：理解 Agent 循环、LLM 流式解析、上下文控制
2. **代码感知（第 5-8 课）**：让 Agent 读懂大型代码库、安全执行、精确编辑、接入生态
3. **架构设计（第 9-11 课）**：微内核、中间件、子 Agent 编排
4. **手写实战（第 12-16 课）**：从零搭建完整的 lite-code 桌面应用

## 运行最终项目

```bash
# 克隆 lite-code 项目
git clone https://github.com/LaynePeng/lite-code.git
cd lite-code

# 安装依赖
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
npm install

# 一键启动
npm run dev
```

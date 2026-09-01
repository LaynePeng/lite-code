# 《AI Code Agent 手把手》— lite-code 从零实战

> 一套从零手写 Code Agent 的完整教程。读完 22 课，你将亲手造出 [lite-code](https://github.com/LaynePeng/lite-code) —— 一个可运行的桌面 Code Agent 应用（Python 内核 + React UI + Electron 外壳）。

## 你将学到

- **不依赖 LangChain 等高层框架**，纯手写 Agent 内核
- LLM 流式 Tool Calling 底层解析、JSON 自愈、死循环检测、Token 预算
- **Prompt 缓存机制**（命中率优化、断点标注、稳定前缀）与 **Token 节省策略**（多层预算、带外存储）
- 代码感知（Ripgrep / Tree-sitter AST）、精确编辑（Search-Replace / Diff）
- 沙箱隔离、动态黑白名单、Web 审批（Human-in-the-Loop）
- **Build/Plan 双默认 Agent** 与用户自定义 Agent 机制
- Cordis 微内核、洋葱中间件、子 Agent 编排
- 多 LLM 供应商接入、FastAPI 服务、React UI、Electron 桌面打包
- 支持系统默认模型与当前会话独立切换模型
- stdio MCP Client、动态工具发现与工具调用
- Electron 真实终端（macOS 使用 shell，Windows 使用 PowerShell）与多窗口项目工作区
- 支持项目根目录 `AGENTS.md` 和 `Claude.md` 指令文件
- 支持项目级/用户级 `skills/*/SKILL.md`，通过 `load_skill` 按需加载技能

## 课程目录

| 模块                    | 课程                                                                  | 主题                                 |
| --------------------- | ------------------------------------------------------------------- | ---------------------------------- |
| **模块一：ReAct 与循环**     | [第 1 课](<01-第1课：LLM 原生接口封装与 Tool Calling 底层流式解析.md>)                  | LLM 原生接口封装 + Tool Calling 流式解析     |
|                       | [第 2 课](<02-第2课：Agent 控制循环的异常容错、死循环中断与 Token 预算控制.md>)                | JSON 自愈 / 死循环检测 / 输出截断（OpenCode 风格）             |
|                       | [第 3 课](<03-第3课：上下文管理、Token 动态计算与 Dynamic System Prompt.md>)          | Token 预算 / 策略 B 两阶段裁剪 / 稳定 System Prompt |
|                       | [第 4 课](<04-第4课：Prompt 缓存机制：命中率优化与工程实践.md>)                       | Prompt 缓存 / 断点标注 / 稳定前缀设计                   |
|                       | [第 5 课](<05-第5课：Token 节省策略：从 OpenSquilla 到缓存友好.md>)                         | 多层预算治理 / 四层压缩 / 带外存储 / 缓存铁律 |
| **模块二：Code Agent 增强** | [第 6 课](<06-第6课：代码感知、Ripgrep 高效搜索与代码库结构构建.md>)                       | Ripgrep 搜索 / 文件树                   |
|                       | [第 7 课](<07-第7课：基于Tree-sitter的抽象语法树（AST）分析与代码依赖图构建.md>)             | Tree-sitter AST 分析                 |
|                       | [第 8 课](<08-第8课：沙箱隔离技术（Execution Sandbox）.md>)                        | 沙箱隔离                               |
|                       | [第 9 课](<09-第9课：精确代码编辑与Apply Patch机制.md>)                           | Search-Replace / Unified Diff      |
|                       | [第 10 课](<10-第10课：项目指令文件与Skills系统.md>)                            | AGENTS.md / CLAUDE.md / Skills 按需加载 |
|                       | [第 11 课](<11-第11课：标准Model Context Protocol（MCP）接入.md>)                | MCP 协议接入                           |
| **模块三：核心架构**          | [第 12 课](<12-第12课：Cordis插件内核设计（Spatiotemporal Composability）.md>)      | Cordis 微内核 / 时空解耦                  |
|                       | [第 13 课](<13-第13课：依赖注入与插件生命周期管理.md>)                                  | 洋葱中间件 / 生命周期钩子                     |
|                       | [第 14 课](<14-第14课：多Agent协作与子Agent调度（Sub-agent Orchestration）.md>) | 子 Agent 编排                         |
|                       | [第 15 课](<15-第15课：Agent类型与自定义机制（Build与Plan与用户扩展）.md>)      | Build/Plan 双默认 Agent / 自定义 Agent 机制 |
| **模块四：手写实战**          | [第 16 课](<16-第16课：核心Core内核编码（lite-code实战第一篇）.md>)                  | Python Core 内核                     |
|                       | [第 17 课](<17-第17课：LLM多供应商适配器与核心工具集（lite-code实战第二篇）.md>)             | 多 LLM + models.dev 元数据 + 20 工具（含 Web 抓取 / Skills）              |
|                       | [第 18 课](<18-第18课：AgentLoop主循环与增强集成（lite-code实战第三篇）.md>)            | AgentLoop 主循环（策略 B 集成 + 上下文可观测性）                      |
|                       | [第 19 课](<19-第19课：安全沙箱与高危拦截实战（lite-code实战第四篇）.md>)                   | 安全沙箱 + Web 审批 + MCP 工具审批                      |
|                       | [第 20 课](<20-第20课：Web UI 实战（lite-code实战第五篇）.md>)                  | FastAPI + React Web UI（上下文情况面板 / 多 Tab）             |
|                       | [第 21 课](<21-第21课：Electron 桌面应用（lite-code实战第六篇）.md>)            | 多窗口项目工作区 / 真实终端 / 打包发布             |
|                       | [第 22 课](<22-第22课：工程实践与踩坑（lite-code实战终章）.md>)                  | 空响应重试 / 配置迁移 / 会话 ID / 强制交付报告             |

## 学习路线

1. **理论入门（第 1-5 课）**：理解 Agent 循环、LLM 流式解析、上下文控制、缓存机制、Token 节省策略
2. **代码感知（第 6-11 课）**：让 Agent 读懂大型代码库、安全执行、精确编辑、项目定制、接入生态
3. **架构设计（第 12-15 课）**：微内核、中间件、子 Agent 编排、Agent 类型与自定义机制
4. **手写实战（第 16-22 课）**：从零搭建完整的 lite-code 桌面应用，以工程实践与踩坑收官

## 运行最终项目

```bash
# 克隆 lite-code 项目
git clone https://github.com/LaynePeng/lite-code.git
cd lite-code

# 安装依赖
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
npm install

# 一键启动（Windows 使用 .venv\Scripts\python.exe）
npm run dev
```

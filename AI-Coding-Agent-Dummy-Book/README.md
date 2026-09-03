# 《AI Code Agent 手把手》— lite-code 从零实战

> 一套从零手写 Code Agent 的完整教程。读完 24 课，你将亲手造出 [lite-code](https://github.com/LaynePeng/lite-code) —— 一个可运行的桌面 Code Agent 应用（Python 内核 + React UI + Electron 外壳）。

## 你将学到

- **不依赖 LangChain 等高层框架**，纯手写 Agent 内核
- LLM 流式 Tool Calling 底层解析、JSON 自愈、死循环检测、Token 预算
- **Prompt 缓存机制**（命中率优化、断点标注、稳定前缀）与 **Token 节省策略**（多层预算、带外存储）
- 代码理解（检索机制 / Tree-sitter AST）、安全代码操作（沙箱隔离 / 精确编辑）
- Skills 系统与权限治理、MCP 协议、插件架构（微内核 / 依赖注入）
- 多 Agent 协作、Build/Plan 双默认 Agent 与用户自定义 Agent 机制
- 多 LLM 供应商接入、FastAPI 服务、React UI、Electron 桌面打包
- 支持全局默认模型与当前会话独立切换模型，主界面显示实际生效的模型与推理档位
- stdio MCP Client、动态工具发现与工具调用
- Electron 真实终端（macOS 使用 shell，Windows 使用 PowerShell）与多窗口项目工作区
- 支持项目根目录 `AGENTS.md` 和 `Claude.md` 指令文件
- 支持项目级/用户级 `skills/*/SKILL.md`，通过 `load_skill` 按需加载技能

## 课程目录

| 模块 | 课程 | 主题 |
| --- | --- | --- |
| **模块一：ReAct 与循环** | [第 1 课](<01-第1课：LLM 原生接口封装与 Tool Calling 底层流式解析.md>) | LLM 原生接口封装 + Tool Calling 流式解析 |
| | [第 2 课](<02-第2课：Agent 控制循环的异常容错、死循环中断与 Token 预算控制.md>) | JSON 自愈 / 死循环检测 / 输出截断（OpenCode 风格） |
| | [第 3 课](<03-第3课：上下文管理、Token 动态计算与 Dynamic System Prompt.md>) | Token 预算 / 策略 B 两阶段裁剪 / 稳定 System Prompt |
| | [第 4 课](<04-第4课：Prompt 缓存机制：命中率优化与工程实践.md>) | Prompt 缓存 / 断点标注 / 稳定前缀设计 |
| | [第 5 课](<05-第5课：Token 节省策略：从 OpenSquilla 到缓存友好.md>) | 多层预算治理 / 四层压缩 / 带外存储 / 缓存铁律 |
| **模块二：代码理解与安全操作** | [第 6 课](<06-第6课：代码理解：检索机制与结构分析.md>) | Ripgrep 检索 / 文件树 / Tree-sitter AST / 骨架压缩 |
| | [第 7 课](<07-第7课：安全代码操作：执行隔离与原子编辑.md>) | 沙箱隔离 / Search-Replace / Unified Diff |
| | [第 8 课](<08-第8课：Skills 系统：注入机制与权限治理.md>) | AGENTS.md / CLAUDE.md / Skills 按需加载 / 权限治理 |
| | [第 9 课](<09-第9课：标准 Model Context Protocol（MCP）接入.md>) | MCP 协议接入 |
| **模块三：核心架构** | [第 10 课](<10-第10课：插件架构：内核设计与依赖注入.md>) | Cordis 微内核 / 时空解耦 / 洋葱中间件 / 生命周期钩子 |
| | [第 11 课](<11-第11课：多 Agent 协作：编排、通信与持久化.md>) | 子 Agent 编排 / 审批透传 / 跨总线转发 |
| | [第 12 课](<12-第12课：Agent 类型与自定义机制.md>) | Build/Plan 双默认 Agent / 自定义 Agent 机制 |
| **模块四：知识篇收尾** | [第 13 课](<13-第13课：知识篇综合复盘.md>) | 知识篇综合复盘 |
| **模块五：手写实战** | [第 14 课](<14-第14课：核心 Core 内核编码（lite-code 实战第一篇）.md>) | Python Core 内核 |
| | [第 15 课](<15-第15课：LLM 多供应商适配器与核心工具集（lite-code 实战第二篇）.md>) | 多 LLM + models.dev 元数据 + 20 工具（含 Web 抓取 / Skills） |
| | [第 16 课](<16-第16课：AgentLoop 主循环与增强集成（lite-code 实战第三篇）.md>) | AgentLoop 主循环（策略 B 集成 + 上下文可观测性） |
| | [第 17 课](<17-第17课：AgentLoop 扩展：工具并行化、排队输入、LLM 故障重试.md>) | 工具并行化 / 排队输入 / LLM 故障重试 |
| | [第 18 课](<18-第18课：安全沙箱与高危拦截实战（lite-code 实战第四篇）.md>) | 安全沙箱 + Web 审批 + MCP 工具审批 |
| | [第 19 课](<19-第19课：人机交互：审批卡与 ask_user 提问.md>) | 审批卡 / ask_user 提问 |
| | [第 20 课](<20-第20课：TODO 清单系统：工具插件、事件驱动与持久化.md>) | TODO 清单系统 / 事件驱动 / 持久化 |
| | [第 21 课](<21-第21课：Web UI 实战（lite-code 实战第五篇）.md>) | FastAPI + React Web UI（上下文情况面板 / 多 Tab） |
| | [第 22 课](<22-第22课：会话管理与综合设置.md>) | 孤儿会话 / 项目切换 / 配置面板 |
| | [第 23 课](<23-第23课：Electron 桌面应用（lite-code 实战第六篇）.md>) | 多窗口项目工作区 / 真实终端 / 打包发布 |
| | [第 24 课](<24-第24课：工程实践与踩坑（lite-code 实战终章）.md>) | 空响应重试 / 配置迁移 / 会话 ID / 强制交付报告 |

## 学习路线

1. **理论入门（第 1-5 课）**：理解 Agent 循环、LLM 流式解析、上下文控制、缓存机制、Token 节省策略
2. **代码理解与安全操作（第 6-9 课）**：代码理解、安全代码操作、Skills 系统与权限治理、MCP 协议
3. **核心架构（第 10-13 课）**：插件架构、多 Agent 协作、Agent 类型、知识篇综合复盘
4. **手写实战（第 14-24 课）**：从零搭建完整的 lite-code 桌面应用，以工程实践与踩坑收官

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
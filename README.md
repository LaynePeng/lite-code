# lite-code

一个手写内核的 Code Agent 桌面应用：Python 内核 + React UI + Electron 外壳，从 LLM 流式解析、上下文压缩到沙箱审批全部纯手写，不依赖 LangChain 等高层框架。

> 版本号单一事实源：`litecode/__init__.py` 的 `__version__`，构建时自动同步到 npm/安装包，页内不再标注具体版本。

📖 配套教程：[《AI Code Agent 手把手》](https://laynepeng.gitbook.io/ai-code-agent-shou-ba-shou)——24 课从零搭建本项目的完整教程（本仓库 `AI-Coding-Agent-Dummy-Book/` 目录）。

## 功能

- **20 个内置工具**：文件读写、Ripgrep 搜索、Tree-sitter AST 大纲、Search-Replace / Unified Diff 精确编辑、受限 Shell、Git 五件套、代码审查、子 Agent 编排、Web 抓取、`load_skill` 技能加载；MCP 工具按配置动态注册
- **多 LLM 供应商**：DeepSeek / OpenAI / Kimi / 通义千问 / 智谱 GLM / Anthropic Claude / 自定义 OpenAI 兼容实例；上下文窗口经 models.dev 元数据自动解析（内置表兜底，断网可用）
- **安全防御**：三级风险模型（SAFE / MEDIUM / HIGH）+ 动态黑白名单热加载 + Web 审批卡（Human-in-the-Loop）；MCP 外部工具默认需用户确认
- **Agent 增强**：JSON 自愈、死循环检测、输出截断落盘（上下文只放句柄）、Token 预算与策略 B 两阶段上下文压缩
- **Prompt 缓存**：断点标注 + 稳定前缀设计；右侧面板实时显示命中率、窗口占用（≥90% 红色警示并自动压缩）与压缩统计
- **Build / Plan 双 Agent**：默认开发 Agent + 只读规划 Agent，支持 `agents/*.json` / `*.md`（frontmatter）自定义
- **项目指令与 Skills**：读取项目根目录 `AGENTS.md` / `CLAUDE.md` 注入 System Prompt；技能索引常驻、`load_skill` 按需加载全文
- **OpenCode 风格交互**：思考/回答分离、工具调用卡片、多 Tab 工作台（对话 + 文件查看）、布局边界可拖拽调整
- **真实终端**：node-pty + xterm.js，macOS 使用 `$SHELL`，Windows 使用 PowerShell；终端不进入会话上下文
- **多窗口项目**：窗口 = 项目 = Core；当前窗口可热切换工作区，也可新窗口打开项目，窗口间共享用户配置
- **多会话管理**：按项目隔离历史会话，JSON 原子写盘持久化，重启后自动加载续聊
- **运行日志**：后端与 Electron 主进程均写入 `~/.lite-code/logs/`（Windows：`C:\Users\<用户名>\.lite-code\logs\`），按 5 MiB 滚动并保留 3 个备份

## 架构

```text
Electron 桌面外壳
├── React Web UI（聊天 / 工具面板 / 审批 / 会话 / 文件树 / 终端）
│   └── HTTP + SSE ↔ Python FastAPI 后端
└── 本地桌面 / 远程 Core / 纯浏览器 三种运行形态

Python 后端（litecode/）
├── core/           内核（事件总线 / 洋葱中间件 / AgentLoop / Token 预算）
├── llm/            LLM 多供应商适配器（手写 SSE 流式解析）
├── tools/          20 个内置工具 + 工具插件
├── mcp/            stdio MCP Client 与管理器
├── security/       安全沙箱（三级风险 / Web 审批）
├── server/         FastAPI 服务（REST + SSE）
└── orchestration/  子 Agent 编排
```

## 使用

```bash
# 前提：Python 3.11+、Node 18+
python3 -m venv .venv
.venv/bin/pip install -e .[dev]    # Windows: .venv\Scripts\pip install -e .[dev]
npm install

npm run dev    # 开发模式：Python Core + Vite + Electron 窗口
npm start      # 生产模式：构建前端 → 自动拉起 Core → 窗口
```

- **API Key**：首次启动后在设置界面选择供应商并填写（存储于 `~/.lite-code/config.json`），也支持 `DEEPSEEK_API_KEY` 等环境变量兜底
- **纯浏览器形态**：`python -m litecode serve` 后访问 `http://127.0.0.1:8787`
- **远程 Core**：`~/.lite-code/client.json` 配置 `coreUrl` 与 Token，窗口直连远程后端

> 国内网络受限时 pip 可加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`；Electron 二进制下载失败时执行 `node node_modules/electron/install.js`（已默认走 npmmirror 镜像）。

## 打包

| 平台 | 命令 | 产物 |
| --- | --- | --- |
| macOS | `npm run package` | `release/lite-code-<版本>-arm64.dmg` |
| Windows | `.\scripts\build-windows.ps1` | `release\lite-code Setup <版本>.exe`（NSIS 安装包） |

两个脚本均支持增量构建：依赖未变化时跳过安装，默认复用 PyInstaller 分析缓存；发布构建加 `--clean`（Windows 为 `-Clean`）。

> 说明：Windows 建议在 Windows 机器上执行打包脚本。
>
> macOS 包未签名（无 Developer ID 证书，未公证）。首次打开如提示「无法验证开发者」，右键应用 →「打开」；如提示「已损坏」，运行 `xattr -dr com.apple.quarantine "/Applications/lite-code.app"` 后重试。
>
> 网络受限环境：electron-builder 下载卡住时可预设 `ELECTRON_MIRROR` / `ELECTRON_BUILDER_BINARIES_MIRROR`（npmmirror），脚本未设置时会自动兜底。

### GitHub Actions 构建（推荐）

仓库已配置 `.github/workflows/release.yml`，无需本地环境即可产出 macOS DMG 与 Windows NSIS 安装包：

1. 推送 `v*` 标签（如 `v1.2.3`，需与 `__version__` 一致）自动触发：构建双平台安装包 → 创建 GitHub Release
2. 构建产物也可在 **Actions** 运行详情页 **Artifacts** 下载：`macos-arm64` / `windows-x64`

安装包从 [Releases](https://github.com/LaynePeng/lite-code/releases) 下载：

| 平台 | 文件 |
| --- | --- |
| macOS (Apple Silicon) | `lite-code-<版本>-arm64.dmg` |
| Windows | `lite-code Setup <版本>.exe` |

## 教程

完整的 24 课从零搭建教程在 `AI-Coding-Agent-Dummy-Book/` 目录：

- 第 1-5 课：LLM 接口封装 / Agent 循环 / Token 预算 / Prompt 缓存 / Token 节省策略
- 第 6-9 课：代码理解 / 安全代码操作 / Skills 系统与权限治理 / MCP 协议
- 第 10-13 课：插件架构 / 多 Agent 协作 / Agent 类型 / 知识篇综合复盘
- 第 14-24 课：手写实战（Core → 多 LLM → AgentLoop → 并行扩展 → 安全 → ask_user → TODO → Web UI → 会话管理 → Electron → 工程实践）

📖 在线阅读：[https://laynepeng.gitbook.io/ai-code-agent-shou-ba-shou](https://laynepeng.gitbook.io/ai-code-agent-shou-ba-shou)

## 技术栈

- [Electron](https://www.electronjs.org/) + [electron-builder](https://www.electron.build/) + [node-pty](https://github.com/microsoft/node-pty) / [xterm.js](https://xtermjs.org/)
- [React 18](https://react.dev/) + [Vite](https://vitejs.dev/) + TypeScript
- [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/)（REST + SSE 流式推送）
- [httpx](https://www.python-httpx.org/)（手写 SSE 流式解析）· [Tree-sitter](https://tree-sitter.github.io/) · [PyInstaller](https://pyinstaller.org/)

## 反馈

问题或建议欢迎到 [GitHub Issues](https://github.com/LaynePeng/lite-code/issues) 反馈。

## License

MIT

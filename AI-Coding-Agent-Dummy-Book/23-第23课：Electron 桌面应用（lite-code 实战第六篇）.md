上一课我们完成了 FastAPI 服务层与 React Web UI，`lite-code` 已经可以在浏览器中使用。但 Code Agent 的最终形态是**桌面应用**：双击图标启动、系统级文件对话框、嵌入式终端、拖拽安装。本课我们用 Electron 为 Web UI 加上桌面外壳：

1. **Electron 主进程**：三种运行形态（本地 Core / 远程 Core / 开发模式）的启动管理；
2. **多窗口项目工作区**：每个窗口绑定独立 Core 与项目，窗口间共享用户配置；
3. **真实终端**：node-pty + xterm.js，在应用内直接跑 shell；
4. **启动体验与崩溃恢复**：加载页、渲染进程崩溃自愈；
5. **一键打包发布**：PyInstaller 后端 + electron-builder 出 .app/.dmg，Windows 增量构建。

#### 1. Electron 桌面外壳 (`electron/main.js`)

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
  // 形态3：本地桌面 —— 每个窗口绑定独立 Python Core
  await createLocalWindow();
}
```

本地 Core 的 spawn 与就绪探测：后端启动后打印 `LITECODE_CORE_READY port=8787` 标记，主进程匹配到该行才知道端口可用——比轮询健康检查更直接，且能同时捕获 stdout/stderr 中的错误信息：

```javascript
// electron/main.js（核心）
const onData = (buf) => {
  const text = buf.toString();
  process.stdout.write(text);
  const m = text.match(/LITECODE_CORE_READY port=(\d+)/);
  if (m && !resolved) {
    resolved = true;
    clearTimeout(timer);
    const port = parseInt(m, 10);
    resolve({ child, url: `http://127.0.0.1:${port}` });
  }
};
```

**一个容易踩的坑：Finder 启动不继承 shell PATH**。从 Dock/Finder 启动的 Electron 应用不会加载 `.zshrc`，`ripgrep` 等通过 Homebrew 安装的二进制不在 PATH 里，Python 后端的工具探测会失败。解决：spawn 时显式拼接常见安装路径：

```javascript
const env = {
  ...process.env,
  PATH: [
    process.env.PATH || "",
    "/opt/homebrew/bin",       // Apple Silicon Homebrew
    "/usr/local/bin",          // Intel Homebrew
    "/opt/local/bin",          // MacPorts
    path.join(process.env.HOME || "", ".cargo/bin"),
    path.join(process.env.HOME || "", ".local/bin"),
  ].filter(Boolean).join(path.delimiter),
  LITECODE_SPAWNED: "1",
};
```

**另一个坑：打包后的 cwd**。`app.getAppPath()` 在打包模式下指向 `app.asar`（一个文件而不是目录），把它作为 spawn 的 cwd 会得到 `ENOTDIR` 错误。因此打包模式必须用 `process.resourcesPath` 作为真实目录：

```javascript
function coreCwd() {
  if (app.isPackaged) return process.resourcesPath;  // 真实目录
  return app.getAppPath();                           // 开发模式：项目根
}
```

**窗口配置**：

```javascript
new BrowserWindow({
  width: 1280, height: 820, minWidth: 960, minHeight: 640,
  backgroundColor: "#0d1117",
  titleBarStyle: "hiddenInset",                       // 无边框 + 红绿灯
  trafficLightPosition: { x: 18, y: 18 },             // 红绿灯避开侧边栏
  webPreferences: { contextIsolation: true, sandbox: true, ... }
})
```

安全基线：`sandbox: true` + `contextIsolation: true` + `nodeIntegration: false`，渲染进程没有任何 Node 能力，桌面功能全部通过 preload 桥（下一节）以白名单 IPC 暴露。远程模式支持 Bearer Token 注入（`session.webRequest.onBeforeSendHeaders`）。

**应用菜单与自定义 About**：Electron 默认的应用菜单里，「关于」走的是 macOS 原生面板——版本信息、图标样式都不受我们控制。生产级应用通常自定义整个菜单模板（`Menu.buildFromTemplate`），把 About 菜单项的 `click` 改为向渲染进程发 IPC，弹出**设计版关于对话框**（品牌图标、版本徽标、GitHub/教程链接，见第 21 课 AboutModal）：

```javascript
// electron/main.js（核心）
function showAbout() {
  for (const instance of localInstances.values()) {
    if (instance.window && !instance.window.isDestroyed()) {
      instance.window.webContents.send("show-about");   // 广播到所有项目窗口
    }
  }
}

function setupMenu() {
  const isMac = process.platform === "darwin";
  const aboutItem = { label: `关于 ${app.name}`, click: showAbout };
  const template = [
    ...(isMac ? [{ label: app.name, submenu: [
      aboutItem, { type: "separator" }, { role: "services" },
      { type: "separator" }, { role: "hide" }, { role: "hideOthers" },
      { role: "unhide" }, { type: "separator" }, { role: "quit" },
    ] }] : []),
    { role: "fileMenu" }, { role: "editMenu" },
    { role: "viewMenu" }, { role: "windowMenu" },
    ...(isMac ? [] : [{ role: "help", submenu: [aboutItem] }]),   // Windows/Linux 放帮助菜单
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(async () => {
  setupMenu();
  // ...窗口与 Core 启动
});
```

渲染侧只需订阅 `show-about` 事件打开弹窗（preload 桥加一个 `onShowAbout`，与其他订阅式接口一样返回取消函数）。注意 `showAbout` 遍历**所有窗口**广播而不是只发当前窗口——多窗口项目工作区下，用户在哪个窗口按菜单都应该响应；发送前的 `isDestroyed()` 检查依旧是硬要求。

#### 2. 多窗口项目工作区

真实使用中，你可能同时开着两三个项目：一个主项目在跑任务，另一个仓库查代码。`lite-code` 的模型是**窗口 = 项目 = Core**：

```javascript
const localInstances = new Map(); // webContents.id -> { window, child, url, workspace }

async function createLocalWindow(workspace = null) {
  const loadingUrl = `file://${path.join(__dirname, "loading.html")}`;
  const window = createWindow(loadingUrl);
  const instance = await spawnLocalCore(workspace);       // 每窗口独立 Core
  const winId = window.webContents.id;
  localInstances.set(winId, { window, ...instance, workspace });
  window.once("closed", () => {                           // 关窗回收自己的 Core
    stopTerminal(winId);
    stopCore(instance.child);
    localInstances.delete(winId);
  });
  window.loadURL(instance.url);
}
```

两条打开项目的路径，语义完全不同：

| 操作 | IPC | 行为 |
|---|---|---|
| 打开项目（当前窗口） | `open-project` | 调用当前 Core 的 `POST /api/workspace` **热切换**，进程不重启 |
| 新窗口打开项目 | `open-project-new-window` | `createLocalWindow(workspace)`，新建窗口 + 新 Core |

**为什么热切换优先？** 重启 Core 意味着重新加载配置、重建 LLM 适配器、丢失内存中的会话统计，冷启动要数秒；热切换只是让现有 Core 换一个工作目录，瞬间完成。但热切换有前置约束——后端在有任务运行时返回 409（上一课的 workspace 切换约束），前端收到错误后提示用户等待任务结束：

```javascript
async function hotSwitchWorkspace(instance, workspace) {
  const resp = await fetch(`${instance.url}/api/workspace`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: workspace }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    return { ok: false, error: body.detail || `切换工作区失败（HTTP ${resp.status}）` };
  }
  instance.workspace = workspace;
  stopTerminal(instance.window.webContents.id);   // 终端跟着切换目录
  return { ok: true };
}
```

注意切换成功后的 `stopTerminal`：旧项目的终端进程 cwd 还在旧目录，必须停掉让前端按新 workspace 重建。

**为什么不加单实例锁？** `app.requestSingleInstanceLock()` 是 Electron 应用的常见默认——第二个实例唤醒已有窗口。但多项目工作流要求每次启动都是新窗口新 Core，所以 `lite-code` 刻意不调用它。代价是用户手动双击图标会开出多个空窗口，换来的是「从 Finder 拖文件夹到图标 → 直接开一个绑定该项目的窗口」这类工作流的可能。

**配置共享**：所有窗口的 Core 都用 `--config-dir ~/.lite-code` 启动，共享同一份模型、安全规则与 Agent 配置——在 A 窗口配置的 API Key，B 窗口立即可用（后端配置热加载，第 15 课）。会话历史则按 workspace 隔离（第 21 课 SessionStore），互不干扰。

#### 3. 真实终端：node-pty + xterm.js

Agent 的 `execute_command` 工具走审批沙箱，但开发者日常还需要一个随时可用的 shell——跑个交互式命令、看看日志、临时 debug。浏览器无法提供真终端（没有 PTY），这正是桌面应用的独家能力。

**架构**：

```text
React TerminalPanel (xterm.js 渲染 ANSI)
      │  terminalInput / terminalResize / terminalStart (ipcRenderer)
      ▼
preload.js —— contextBridge 白名单桥
      │  ipcMain.on / ipcMain.handle
      ▼
Electron 主进程 —— node-pty.spawn(shell)
      │  term.onData → event.sender.send("terminal-data", data)
      ▼
真实 shell 进程（macOS: $SHELL / Windows: powershell.exe）
```

**主进程侧**（node-pty 模拟伪终端，让 shell 认为自己挂在真终端上，颜色/交互/信号全部正常）：

```javascript
// electron/main.js（核心）
ipcMain.handle("terminal-start", (event, cols = 100, rows = 24) => {
  const instance = localInstances.get(event.sender.id);
  if (!instance?.workspace) return { ok: false, error: "请先打开项目" };
  stopTerminal(event.sender.id);                      // 一个窗口一个终端
  const shellName = process.platform === "win32" ? "powershell.exe" : (process.env.SHELL || "/bin/zsh");
  const term = pty.spawn(shellName, [], {
    name: "xterm-256color", cols, rows,
    cwd: instance.workspace, env: process.env,        // 终端跟着项目走
  });
  const winId = event.sender.id;
  const sender = event.sender;
  // 窗口关闭后 webContents 已销毁，但 pty 回调可能仍触发（kill 异步 + 输出缓冲）。
  // 对已销毁对象调用 send 会抛 "Object has been destroyed"，必须逐次检查存活。
  const safeSend = (channel, payload) => {
    if (!sender.isDestroyed()) sender.send(channel, payload);
  };
  terminals.set(winId, term);
  term.onData((data) => safeSend("terminal-data", data));
  term.onExit(({ exitCode }) => {
    safeSend("terminal-exit", exitCode);
    terminals.delete(winId);
  });
  return { ok: true };
});
ipcMain.on("terminal-input", (event, data) => terminals.get(event.sender.id)?.write(data));
ipcMain.on("terminal-resize", (event, cols, rows) => terminals.get(event.sender.id)?.resize(cols, rows));
```

四个设计细节：

1. **按 `webContents.id` 隔离**：每个窗口一个终端实例，`Map<id, pty>` 管理；窗口关闭 / 热切换项目时 `stopTerminal` 回收，避免孤儿 shell 进程；
2. **销毁检查是硬要求，不是可选项**：`term.kill()` 是异步的——窗口关闭后，pty 的输出缓冲还会触发若干次 `onData`，而此时 `webContents` 已经销毁，直接 `sender.send()` 必然抛 `Object has been destroyed` 崩掉主进程。这与 `render-process-gone` 里 reload 前检查 `isDestroyed()` 是同一条铁律：**事件回调触发时，引用的对象可能已经不在了**；
3. **shell 选择**：macOS 用 `$SHELL`（通常是 zsh），Windows 用 PowerShell——不假设用户环境；
4. **终端不属于会话**：终端输入输出完全不进入 Agent 的会话历史与上下文（它不经过 Python Core），跑 `npm run dev` 产生几万行日志也不会污染 Agent 的 Token 预算。

**preload 桥**：终端数据是高频双向流，用 `ipcRenderer.on` 订阅 + 返回取消函数（而不是一次性 promise）：

```javascript
// electron/preload.js（核心）
contextBridge.exposeInMainWorld("liteCode", {
  terminalStart: (cols, rows) => ipcRenderer.invoke("terminal-start", cols, rows),
  terminalInput: (data) => ipcRenderer.send("terminal-input", data),
  terminalResize: (cols, rows) => ipcRenderer.send("terminal-resize", cols, rows),
  terminalStop: () => ipcRenderer.send("terminal-stop"),
  onTerminalData: (listener) => {
    const handler = (_event, data) => listener(data);
    ipcRenderer.on("terminal-data", handler);
    return () => ipcRenderer.removeListener("terminal-data", handler);   // 取消订阅
  },
  onTerminalExit: (listener) => { ... },
});
```

**渲染侧**（`web/src/components/TerminalPanel.tsx`）：xterm.js 渲染 + FitAddon 自适应宽度。关键是 **ResizeObserver → fit() → resize()** 的链路——容器尺寸变化时先让 xterm 重排列数，再把新 cols/rows 同步给 pty，否则 shell 的换行位置会和显示错位：

```tsx
useEffect(() => {
  const bridge = window.liteCode;
  if (!bridge || !host || !workspace) return;

  const terminal = new Terminal({ convertEol: true, cursorBlink: true, ... });
  const fit = new FitAddon();
  terminal.loadAddon(fit);
  terminal.open(host);
  fit.fit();

  const offData = bridge.onTerminalData((data) => terminal.write(data));   // pty → 屏幕
  const input = terminal.onData((data) => bridge.terminalInput(data));     // 键盘 → pty
  const resize = new ResizeObserver(() => {
    fit.fit();
    bridge.terminalResize(terminal.cols, terminal.rows);
  });
  resize.observe(host);
  void bridge.terminalStart(terminal.cols, terminal.rows);

  return () => {                                                          // 卸载即回收
    resize.disconnect();
    input.dispose();
    offData();
    bridge.terminalStop();
    terminal.dispose();
  };
}, [workspace]);

if (!window.liteCode) return <div>终端仅在桌面应用中可用</div>;   // 浏览器形态优雅降级
```

最后一行是**能力探测式降级**：`window.liteCode` 只有在 Electron preload 注入后才存在。同一个 React 组件在纯浏览器形态下显示提示而不是报错——Web UI 不感知运行环境（上一课的原则），桌面能力按需存在。

#### 4. 启动体验与崩溃恢复

桌面应用在真实环境中会遇到各种异常：后端启动慢、渲染进程崩溃、页面加载失败。

**Electron 启动加载页**（`electron/loading.html`）

PyInstaller 后端需要加载 Python 运行时、依赖和应用配置；Windows 上还可能受到实时杀毒扫描影响。如果等后端就绪再创建窗口，用户会看到一片空白。解决：窗口**立即创建**，显示内置加载页，后端就绪后自动跳转主界面：

```html
<!-- electron/loading.html（核心结构） -->
<body>
  <!-- 应用图标：内联 SVG（与 scripts/app-icon.svg 同源），与侧边栏/空状态/关于弹窗共用同一图形 -->
  <div class="logo"><svg viewBox="0 0 1024 1024" width="76" height="76">…渐变底 + 代码括号 + 光标…</svg></div>
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

主进程启动逻辑：先创建窗口加载 loading.html，再 await 后端就绪：

```javascript
const loadingUrl = `file://${path.join(__dirname, "loading.html")}`;
const window = createWindow(loadingUrl);        // 立即显示
const instance = await spawnLocalCore(workspace); // 可能耗时 30s
window.loadURL(instance.url);                   // 就绪后跳转主界面
```

60s 后端启动超时兜底：超时 SIGKILL 后端进程并报错，加载页显示重试按钮，不会永久卡在"正在启动"。

**渲染进程崩溃自动恢复**（`electron/main.js`）

Electron 渲染进程可能因各种原因崩溃（白屏）。监听 `render-process-gone` 事件，1s 后自动 reload；页面加载失败时最多重试 3 次：

```javascript
window.webContents.on("render-process-gone", (event, details) => {
  setTimeout(() => {
    if (window && !window.isDestroyed()) window.reload();
  }, 1000);
});

let failCount = 0;
window.webContents.on("did-fail-load", (event, code, desc) => {
  if (++failCount <= 3) {
    setTimeout(() => window?.reload(), 2000);
  }
});
window.webContents.on("did-finish-load", () => { failCount = 0; });
```

注意两处 `isDestroyed()` 检查：定时器回调触发时窗口可能已被用户关闭，对已销毁对象调用 reload 会抛异常。这是事件 + 定时器组合的通用陷阱——**异步回调里必须重新校验对象存活状态**。

窗口关闭时的完整回收链：`closed` 事件 → 停终端（pty）→ 停后端（SIGTERM）→ 从 `localInstances` 移除。应用退出（`window-all-closed`）时遍历回收所有 Core，避免 Python 进程泄漏成孤儿。

#### 5. 一键打包与 Windows 加速 (`npm run package`)

```bash
npm run build:web                    # 构建 React 前端 → web/dist/
node scripts/package-backend.mjs     # PyInstaller --onedir，默认复用分析缓存
node scripts/package.mjs             # macOS 完整打包：图标 → 后端 → .app → DMG
```

**PyInstaller 使用 `--onedir`**：产出目录结构（`release/backend/lite-code-backend/lite-code-backend + _internal/`），启动时不需要解压。常规构建不传 `--clean`，复用 `release/_build` 的分析缓存；需要完全重建时显式传 `--clean`：

```javascript
// electron/main.js — 查找 onedir 后端
function resolvePython() {
  if (app.isPackaged) {
    const dir = path.join(process.resourcesPath, "litecode-bin", "lite-code-backend");
    const bundled = path.join(dir, process.platform === "win32" ? "lite-code-backend.exe" : "lite-code-backend");
    if (fs.existsSync(bundled)) return bundled;
  }
  ...
}
```

**node-pty 的打包陷阱**：node-pty 是含 C++ 原生绑定的模块，编译产物（`.node` 文件）不能被打进 asar 归档——Electron 运行时无法从 asar 内加载原生模块。package.json 里必须显式解包：

```json
{
  "build": {
    "files": ["electron/**/*", "package.json", "node_modules/node-pty/**/*"],
    "asarUnpack": ["node_modules/node-pty/**/*"]
  }
}
```

同样地，PyInstaller 打包 Python 后端时也要确认 `ripgrep` 等外部二进制进入 `--add-binary` 清单，否则打包版会静默降级到纯 Python 搜索（第 6 课的工具探测顺序在打包场景依然生效）。

**Windows 增量打包**：`scripts/build-windows.ps1` 会对 `pyproject.toml` 计算 SHA-256 指纹。依赖清单未变化时跳过 pip 安装；首次安装或依赖变化时使用标准隔离构建，避免全新 runner 缺少构建工具导致原生包安装失败。根目录和 `web/` 存在 lockfile 时使用 `npm ci`，依赖目录不存在才安装。默认复用 PyInstaller 缓存，发布构建才加 `-Clean`：

```powershell
# build-windows.ps1 用法
.\scripts\build-windows.ps1             # 增量构建，默认复用缓存
.\scripts\build-windows.ps1 -Clean      # 发布构建，清理 PyInstaller 分析缓存
```

**macOS 打包脚本 `package.mjs` 带进度显示**：每步打印耗时 (`(x.xs)`)，让用户知道在哪一步——icon/后端/electron-builder/hdiutil：

```text
[package] Step 0/4: Generate app icon
[1245] icon... (0.3s)
[package] Step 1/4: Package backend binary
[1723] PyInstaller --onedir... (35.2s)
[package] Step 2/4: electron-builder --dir produces .app
[5312] electron-builder... (2.5s)
[package] Step 3/4: Wrap DMG with hdiutil
[7641] prepare... (0.2s)
[7893] hdiutil create... (18.1s)
[package] Done -> release/lite-code-<版本>-arm64.dmg
```

**产物**（以 macOS 为例）：版本唯一来源是 `litecode/__init__.py` 中的 `__version__`，构建时 `scripts/sync-version.mjs` 会同步 npm 元数据，DMG 名称读取同步后的版本。
```
release/
├── lite-code-<版本>-arm64.dmg          (安装包)
└── mac-arm64/lite-code.app             (解包目录，可直接运行)
```

关键工程点：
- **后端进包**：`extraResources` 把 `release/backend/lite-code-backend` 复制到 `resources/litecode-bin/lite-code-backend`，Electron 主进程通过 `resolvePython()` 使用内置二进制；
- **图标**：`scripts/app-icon.svg` 直接配置为 `win.icon`，electron-builder 自动栅格化为 ICO；
- **加载页**：`--onedir` 直接携带依赖目录，启动时无需解压，配合 loading.html 明确展示后端就绪进度。

**配置目录**：Core 的默认配置目录为 `~/.lite-code`，其中保存模型、API Key、安全规则、模型元数据和会话历史。`--config-dir` 可用于测试或显式隔离配置；正常桌面启动始终使用用户目录。

**运行日志**：后端与 Electron 主进程均写入 `~/.lite-code/logs/`。`lite-code.log` 记录 Python Core 日志，`electron.log` 记录 Electron 主进程及其转发的本地 Core 输出；单文件达到 5 MiB 后滚动，保留 3 个备份。Windows 对应目录为 `C:\\Users\\<用户名>\\.lite-code\\logs\\`。

**开发模式**：`npm run dev`（concurrently 编排 Python Core + Vite + Electron，一行命令三步启动）

### 本课小结

在本课中，我们为 `lite-code` 完成了桌面化：

1. **Electron 主进程**：三种运行形态、就绪标记探测、Finder PATH 陷阱、asar cwd 陷阱；
2. **多窗口项目工作区**：窗口 = 项目 = Core 的映射、热切换优先于重启、无单实例锁的多开取舍、配置共享与历史隔离；
3. **真实终端**：node-pty + xterm.js 的完整数据链路、按窗口隔离与进程回收、ResizeObserver 同步、能力探测式降级；
4. **启动体验**：加载页先行、崩溃自愈、异步回调中的存活校验、退出回收链；
5. **打包发布**：PyInstaller `--onedir`、node-pty asarUnpack、Windows 指纹增量构建、DMG 产物。

至此，`lite-code` 已经是一个可以分发给别人安装使用的完整桌面应用。但它离"生产可用"还差最后一课——真实用户使用中暴露的那些工程问题。下一课我们将开启 **第24课：工程实践与踩坑（lite-code 实战终章）** —— 复盘开发过程中真实踩过的坑，并为整套课程画上句号！

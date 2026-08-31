// lite-code Electron 主进程
// 职责：
//   1. 读取客户端配置（~/.lite-code/client.json），支持远程 Core 直连
//   2. 无配置时自动拉起本地 Python Core（打包后使用内置二进制）
//   3. 等待 LITECODE_CORE_READY 就绪标记后打开窗口
//   4. 「打开项目」：为每个项目窗口启动独立 Core，不影响原窗口
//   5. 一个 workspace 可打开多个窗口；每个窗口的任务独立，Tab 是窗口内工作台状态
//   6. 退出时回收各窗口对应的后端进程
const { app, BrowserWindow, session, shell, dialog, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CLIENT_CONFIG = path.join(os.homedir(), ".lite-code", "client.json");

let mainWindow = null;
// 本地模式下每个窗口对应一个独立 Core。即使多个窗口打开同一 workspace，
// 它们的任务和实时状态也独立；会话/文件 Tab 仅属于各自渲染窗口。
const localInstances = new Map(); // BrowserWindow -> { child, url }
let coreMode = "local"; // "local" | "remote" | "dev"
let coreUrl = ""; // 当前后端 HTTP 地址（热切换工作区用）

function loadClientConfig() {
  try {
    if (fs.existsSync(CLIENT_CONFIG)) {
      return JSON.parse(fs.readFileSync(CLIENT_CONFIG, "utf-8"));
    }
  } catch (err) {
    console.warn("[lite-code] 客户端配置读取失败:", err.message);
  }
  return { coreUrl: "", token: "" };
}

function createWindow(url) {
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#0d1117",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 18, y: 18 },
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  // 立即显示窗口 + 内置加载页，避免等待后端就绪时空白
  window.once("ready-to-show", () => window.show());
  window.loadURL(url);
  window.webContents.setWindowOpenHandler(({ url: target }) => {
    shell.openExternal(target);
    return { action: "deny" };
  });
  // preload 注入失败时输出错误，便于排查
  window.webContents.on("preload-error", (event, preloadPath, error) => {
    console.error(`[lite-code] preload 加载失败: ${preloadPath}`, error.message);
  });
  // 渲染进程崩溃 / 白屏自动恢复
  window.webContents.on("render-process-gone", (event, details) => {
    console.error("[lite-code] 渲染进程异常:", details.reason);
    setTimeout(() => {
      if (window && !window.isDestroyed()) {
        window.reload();
      }
    }, 1000);
  });
  // 页面加载失败（后端未就绪等）自动重试
  let failCount = 0;
  window.webContents.on("did-fail-load", (event, code, desc) => {
    failCount += 1;
    console.warn(`[lite-code] 页面加载失败(${code}): ${desc}`);
    if (failCount <= 3) {
      setTimeout(() => {
        if (window && !window.isDestroyed()) window.reload();
      }, 2000);
    }
  });
  window.webContents.on("did-finish-load", () => {
    failCount = 0;
  });
  return window;
}

function resolvePython() {
  // 打包模式：使用内置后端二进制（PyInstaller --onedir 结构：litecode-bin/lite-code-backend/lite-code-backend）
  if (app.isPackaged) {
    const isWin = process.platform === "win32";
    const exe = isWin ? "lite-code-backend.exe" : "lite-code-backend";
    const dir = path.join(process.resourcesPath, "litecode-bin", "lite-code-backend");
    const bundled = path.join(dir, exe);
    if (fs.existsSync(bundled)) return bundled;
    // 兼容旧版单文件
    const legacy = path.join(process.resourcesPath, "litecode-bin", exe);
    if (fs.existsSync(legacy)) return legacy;
  }
  // 开发模式：优先项目 venv，其次系统 python3
  const projectRoot = app.getAppPath();
  const venv = process.platform === "win32"
    ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
    : path.join(projectRoot, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return process.platform === "win32" ? "python" : "python3";
}

// cwd 必须是真实目录：打包模式下 app.getAppPath() 指向 app.asar（文件），会导致 spawn ENOTDIR
function coreCwd() {
  if (app.isPackaged) return process.resourcesPath;
  return app.getAppPath();
}

function stopCore(child) {
  if (child) {
    try {
      child.kill("SIGTERM");
    } catch {
      /* ignore */
    }
  }
}

function spawnLocalCore(workspace) {
  return new Promise((resolve, reject) => {
    const python = resolvePython();
    const projectRoot = coreCwd();
    const args = app.isPackaged
      ? ["serve", "--port", "0"]
      : ["-m", "litecode", "serve", "--port", "0"];
    if (workspace) args.push("--workspace", workspace);
    const env = {
      ...process.env,
      // Finder 启动 Electron 时不会继承 shell PATH；覆盖 Homebrew/MacPorts
      // 等常见 ripgrep 安装位置，Python 后端也会继续使用绝对路径探测。
      PATH: [
        process.env.PATH || "",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/opt/local/bin",
        path.join(process.env.HOME || "", ".cargo/bin"),
        path.join(process.env.HOME || "", ".local/bin"),
      ].filter(Boolean).join(path.delimiter),
      LITECODE_SPAWNED: "1",
    };

    console.log(`[lite-code] 启动本地 Core: ${python} ${args.join(" ")}`);
    const child = spawn(python, args, {
      cwd: projectRoot,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let resolved = false;
    const timer = setTimeout(() => {
      if (!resolved) {
        child.kill("SIGKILL");
        reject(new Error("后端启动超时（60s 内未就绪）"));
      }
    }, 60000);

    const onData = (buf) => {
      const text = buf.toString();
      process.stdout.write(text);
      const m = text.match(/LITECODE_CORE_READY port=(\d+)/);
      if (m && !resolved) {
        resolved = true;
        clearTimeout(timer);
        const port = parseInt(m[1], 10);
        resolve({ child, url: `http://127.0.0.1:${port}` });
      }
    };

    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("exit", (code) => {
      if (!resolved) {
        clearTimeout(timer);
        reject(new Error(`后端进程提前退出（code=${code}）`));
      }
    });
  });
}

function injectRemoteToken(token) {
  if (!token) return;
  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    details.requestHeaders["Authorization"] = `Bearer ${token}`;
    callback({ requestHeaders: details.requestHeaders });
  });
}

// ------------------------------------------------------------ 打开项目

async function openLocalWorkspace(workspace) {
  const loadingUrl = `file://${path.join(__dirname, "loading.html")}`;
  const window = createWindow(loadingUrl);
  let instance = null;
  let closed = false;
  window.once("closed", () => {
    closed = true;
    if (instance) {
      localInstances.delete(window);
      stopCore(instance.child);
    }
  });
  try {
    instance = await spawnLocalCore(workspace);
    if (closed) {
      stopCore(instance.child);
      return { ok: false, error: "项目窗口已关闭" };
    }
    localInstances.set(window, instance);
    if (!window.isDestroyed()) window.loadURL(instance.url);
    console.log(`[lite-code] 已打开独立项目窗口 → ${workspace} (${instance.url})`);
    return { ok: true, url: instance.url, workspace };
  } catch (err) {
    console.error(`[lite-code] 打开项目失败 (${workspace}):`, err.message);
    if (!window.isDestroyed()) {
      window.webContents.executeJavaScript(`
        document.querySelector('.spinner').style.display='none';
        document.querySelector('.progress').style.display='none';
        document.querySelector('.error').style.display='flex';
      `).catch(() => {});
    }
    return { ok: false, error: err.message };
  }
}

async function handleOpenProject(event) {
  // 本地 Core 模式下始终打开新窗口，不按 workspace 去重：同一项目可多窗口并行。
  // 远程/开发模式仍不提供本地项目切换。
  if (coreMode !== "local") {
    return { ok: false, error: "当前为远程/开发模式，不支持切换工作区" };
  }

  const owner = BrowserWindow.fromWebContents(event.sender) || mainWindow;
  const result = await dialog.showOpenDialog(owner, {
    title: "选择要打开的项目目录",
    buttonLabel: "打开项目",
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return { ok: false, error: "cancelled" };
  }
  const workspace = result.filePaths[0];
  return openLocalWorkspace(workspace);
}

ipcMain.handle("open-project", handleOpenProject);

// ------------------------------------------------------------ 启动

app.whenReady().then(async () => {
  // 开发模式：直接加载 Vite dev server
  if (process.env.LITECODE_DEV_URL) {
    coreMode = "dev";
    mainWindow = createWindow(process.env.LITECODE_DEV_URL);
    app.on("window-all-closed", () => app.quit());
    return;
  }

  const config = loadClientConfig();

  // 形态2：远程 Core
  if (config.coreUrl) {
    coreMode = "remote";
    injectRemoteToken(config.token);
    console.log(`[lite-code] 连接远程 Core: ${config.coreUrl}`);
    mainWindow = createWindow(config.coreUrl);
    app.on("window-all-closed", () => app.quit());
    return;
  }

  // 形态1：本地 Core
  coreMode = "local";

  // 立即创建窗口 + 加载页（即使后端未就绪）
  const loadingUrl = `file://${path.join(__dirname, "loading.html")}`;
  mainWindow = createWindow(loadingUrl);
  console.log("[lite-code] 窗口已创建，正在启动后端…");

  try {
    const instance = await spawnLocalCore();
    const window = mainWindow;
    localInstances.set(window, instance);
    window.once("closed", () => {
      localInstances.delete(window);
      stopCore(instance.child);
    });
    console.log(`[lite-code] 本地 Core 就绪 → ${instance.url}`);
    if (window && !window.isDestroyed()) window.loadURL(instance.url);
  } catch (err) {
    console.error("[lite-code] 启动失败:", err.message);
    // 加载页显示错误
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.executeJavaScript(`
        document.querySelector('.spinner').style.display='none';
        document.querySelector('.progress').style.display='none';
        document.querySelector('.error').style.display='flex';
      `);
    }
  }
});

app.on("window-all-closed", () => {
  for (const instance of localInstances.values()) stopCore(instance.child);
  localInstances.clear();
  if (process.platform !== "darwin") app.quit();
});

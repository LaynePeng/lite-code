// lite-code Electron 主进程
// 职责：
//   1. 读取客户端配置（~/.lite-code/client.json），支持远程 Core 直连
//   2. 无配置时自动拉起本地 Python Core（打包后使用内置二进制）
//   3. 等待 LITECODE_CORE_READY 就绪标记后打开窗口
//   4. 「打开项目」：通过当前 Core 热切换 workspace，不新建或重启后端
//   5. 退出时回收后端进程
const { app, BrowserWindow, session, shell, dialog, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CLIENT_CONFIG = path.join(os.homedir(), ".lite-code", "client.json");

let mainWindow = null;
let coreChild = null; // 当前本地后端进程
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
  mainWindow = new BrowserWindow({
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
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.loadURL(url);
  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    shell.openExternal(target);
    return { action: "deny" };
  });
  // preload 注入失败时输出错误，便于排查
  mainWindow.webContents.on("preload-error", (event, preloadPath, error) => {
    console.error(`[lite-code] preload 加载失败: ${preloadPath}`, error.message);
  });
  // 渲染进程崩溃 / 白屏自动恢复
  mainWindow.webContents.on("render-process-gone", (event, details) => {
    console.error("[lite-code] 渲染进程异常:", details.reason);
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.reload();
      }
    }, 1000);
  });
  // 页面加载失败（后端未就绪等）自动重试
  let failCount = 0;
  mainWindow.webContents.on("did-fail-load", (event, code, desc) => {
    failCount += 1;
    console.warn(`[lite-code] 页面加载失败(${code}): ${desc}`);
    if (failCount <= 3) {
      setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload();
      }, 2000);
    }
  });
  mainWindow.webContents.on("did-finish-load", () => {
    failCount = 0;
  });
  return mainWindow;
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

function stopCore() {
  if (coreChild) {
    try {
      coreChild.kill("SIGTERM");
    } catch {
      /* ignore */
    }
    coreChild = null;
  }
}

function spawnLocalCore(workspace) {
  return new Promise((resolve, reject) => {
    const python = resolvePython();
    const projectRoot = coreCwd();
    const args = app.isPackaged
      ? ["serve", "--port", "0"]
      : ["-m", "litecode", "serve", "--port", "0"];
    args.push("--config-dir", path.join(os.homedir(), ".lite-code"));
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
        coreChild = child;
        coreUrl = `http://127.0.0.1:${port}`;
        resolve({ child, url: coreUrl });
      }
    };

    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("exit", (code) => {
      if (!resolved) {
        clearTimeout(timer);
        reject(new Error(`后端进程提前退出（code=${code}）`));
      }
      if (coreChild === child) coreChild = null;
    });

    app.on("will-quit", () => {
      try {
        child.kill("SIGTERM");
      } catch {
        /* ignore */
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

// 热切换工作区：调用当前后端的 /api/workspace，进程不重启（快）
async function hotSwitchWorkspace(workspace) {
  if (!coreUrl) return false;
  try {
    const resp = await fetch(`${coreUrl}/api/workspace`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: workspace }),
    });
    if (!resp.ok) return false;
    const data = await resp.json();
    if (!data.ok) return false;
    console.log(`[lite-code] 热切换工作区 → ${workspace}`);
    return true;
  } catch (err) {
    console.warn("[lite-code] 热切换工作区失败:", err.message);
    return false;
  }
}

async function handleOpenProject() {
  // 仅本地 Core 形态支持切换工作区
  if (coreMode !== "local") {
    return { ok: false, error: "当前为远程/开发模式，不支持切换工作区" };
  }

  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择要打开的项目目录",
    buttonLabel: "打开项目",
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return { ok: false, error: "cancelled" };
  }
  const workspace = result.filePaths[0];

  // 只允许当前 Core 热切换，避免新进程丢失现有模型配置和运行状态。
  if (await hotSwitchWorkspace(workspace)) {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.loadURL(coreUrl);
    return { ok: true, url: coreUrl, workspace };
  }
  return { ok: false, error: "切换工作区失败，当前 Core 和模型配置保持不变" };
}

ipcMain.handle("open-project", handleOpenProject);

// ------------------------------------------------------------ 启动

app.whenReady().then(async () => {
  // 开发模式：直接加载 Vite dev server
  if (process.env.LITECODE_DEV_URL) {
    coreMode = "dev";
    createWindow(process.env.LITECODE_DEV_URL);
    app.on("window-all-closed", () => app.quit());
    return;
  }

  const config = loadClientConfig();

  // 形态2：远程 Core
  if (config.coreUrl) {
    coreMode = "remote";
    injectRemoteToken(config.token);
    console.log(`[lite-code] 连接远程 Core: ${config.coreUrl}`);
    createWindow(config.coreUrl);
    app.on("window-all-closed", () => app.quit());
    return;
  }

  // 形态1：本地 Core
  coreMode = "local";

  // 立即创建窗口 + 加载页（即使后端未就绪）
  const loadingUrl = `file://${path.join(__dirname, "loading.html")}`;
  createWindow(loadingUrl);
  console.log("[lite-code] 窗口已创建，正在启动后端…");

  try {
    const { url } = await spawnLocalCore();
    console.log(`[lite-code] 本地 Core 就绪 → ${url}`);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(url);
    }
    app.on("window-all-closed", () => {
      stopCore();
      app.quit();
    });
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
  if (process.platform !== "darwin") app.quit();
});

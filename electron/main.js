// lite-code Electron 主进程
// 职责：
//   1. 读取客户端配置（~/.lite-code/client.json），支持远程 Core 直连
//   2. 无配置时自动拉起本地 Python Core（打包后使用内置二进制）
//   3. 等待 LITECODE_CORE_READY 就绪标记后打开窗口
//   4. 「打开项目」：系统目录选择框 → 重启后端（新 workspace）→ 窗口重新加载
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
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  mainWindow.loadURL(url);
  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    shell.openExternal(target);
    return { action: "deny" };
  });
  // preload 注入失败时输出错误，便于排查
  mainWindow.webContents.on("preload-error", (event, preloadPath, error) => {
    console.error(`[lite-code] preload 加载失败: ${preloadPath}`, error.message);
  });
  return mainWindow;
}

function resolvePython() {
  // 打包模式：使用内置后端二进制
  if (app.isPackaged) {
    const bundled = path.join(process.resourcesPath, "litecode-bin", "lite-code-backend");
    if (fs.existsSync(bundled)) return bundled;
  }
  // 开发模式：优先项目 venv，其次系统 python3
  const projectRoot = app.getAppPath();
  const venv = path.join(projectRoot, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return "python3";
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
    if (workspace) args.push("--workspace", workspace);
    const env = {
      ...process.env,
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

  stopCore();
  try {
    const { url } = await spawnLocalCore(workspace);
    console.log(`[lite-code] 已切换工作区 → ${workspace} (${url})`);
    if (mainWindow) mainWindow.loadURL(url);
    return { ok: true, url, workspace };
  } catch (err) {
    console.error("[lite-code] 切换工作区失败:", err.message);
    return { ok: false, error: err.message };
  }
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
  try {
    const { url } = await spawnLocalCore();
    console.log(`[lite-code] 本地 Core 就绪 → ${url}`);
    createWindow(url);
    app.on("window-all-closed", () => {
      stopCore();
      app.quit();
    });
  } catch (err) {
    console.error("[lite-code] 启动失败:", err.message);
    app.quit();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
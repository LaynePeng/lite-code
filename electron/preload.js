// 最小化 preload：仅暴露「打开项目」等安全的原生能力
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("liteCode", {
  platform: process.platform,
  version: process.env.LITECODE_VERSION || "0.11.0",
  /**
   * 打开系统目录选择框并切换工作区。
   * 返回 { ok: true, url } 或 { ok: false, error }；用户在对话框取消时返回 { ok: false, error: "cancelled" }。
   */
  openProject: () => ipcRenderer.invoke("open-project"),
  openProjectNewWindow: () => ipcRenderer.invoke("open-project-new-window"),
  terminalStart: (cols, rows) => ipcRenderer.invoke("terminal-start", cols, rows),
  terminalInput: (data) => ipcRenderer.send("terminal-input", data),
  terminalResize: (cols, rows) => ipcRenderer.send("terminal-resize", cols, rows),
  terminalStop: () => ipcRenderer.send("terminal-stop"),
  onTerminalData: (listener) => {
    const handler = (_event, data) => listener(data);
    ipcRenderer.on("terminal-data", handler);
    return () => ipcRenderer.removeListener("terminal-data", handler);
  },
  onTerminalExit: (listener) => {
    const handler = (_event, code) => listener(code);
    ipcRenderer.on("terminal-exit", handler);
    return () => ipcRenderer.removeListener("terminal-exit", handler);
  },
  /** 订阅应用菜单「关于」点击，打开设计版关于弹窗；返回取消订阅函数 */
  onShowAbout: (listener) => {
    const handler = () => listener();
    ipcRenderer.on("show-about", handler);
    return () => ipcRenderer.removeListener("show-about", handler);
  },
});

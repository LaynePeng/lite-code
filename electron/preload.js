// 最小化 preload：仅暴露「打开项目」等安全的原生能力
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("liteCode", {
  platform: process.platform,
  version: process.env.LITECODE_VERSION || "0.6.1-rc0",
  /**
   * 打开系统目录选择框并切换工作区。
   * 返回 { ok: true, url } 或 { ok: false, error }；用户在对话框取消时返回 { ok: false, error: "cancelled" }。
   */
  openProject: () => ipcRenderer.invoke("open-project"),
});
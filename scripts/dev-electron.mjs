// 跨平台启动 Electron（dev 模式）：注入 LITECODE_DEV_URL 指向 Vite dev server
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
// electron 包导出的字符串即真实二进制路径（Windows 为 dist/electron.exe）
const require = createRequire(import.meta.url);
const electronPath = require("electron");
const child = spawn(electronPath, ["."], {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, LITECODE_DEV_URL: "http://localhost:5173" },
});
child.on("exit", (code) => process.exit(code ?? 0));
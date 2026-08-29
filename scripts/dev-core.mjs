// 跨平台启动本地 Core（dev 模式）：解析 venv 内的 python 路径
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const isWindows = process.platform === "win32";
const python = isWindows
  ? path.join(root, ".venv", "Scripts", "python.exe")
  : path.join(root, ".venv", "bin", "python");

if (!fs.existsSync(python)) {
  console.error(`[dev] 未找到 venv Python: ${python}`);
  console.error("[dev] 请先执行: python -m venv .venv && .venv/Scripts/pip install -e .");
  process.exit(1);
}

const args = ["-m", "litecode", "serve", "--port", "8787", "--log-level", "info"];
console.log(`[dev] ${python} ${args.join(" ")}`);
const child = spawn(python, args, { cwd: root, stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 0));
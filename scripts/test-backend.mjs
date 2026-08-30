// Cross-platform backend test runner: resolve python inside venv and run pytest
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
  console.error(`[test] venv Python not found: ${python}`);
  process.exit(1);
}

const child = spawn(python, ["-m", "pytest", "-q"], { cwd: root, stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 0));
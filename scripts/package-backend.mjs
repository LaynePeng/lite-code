// 打包后端：PyInstaller 产出独立二进制（release/backend/lite-code-backend）
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const python = path.join(root, ".venv", "bin", "python");
const outDir = path.join(root, "release", "backend");

if (!fs.existsSync(python)) {
  console.error("[package] 未找到 .venv/bin/python，请先执行: python3 -m venv .venv && .venv/bin/pip install -e .");
  process.exit(1);
}

fs.mkdirSync(outDir, { recursive: true });

const webDist = path.join(root, "web", "dist");
if (!fs.existsSync(path.join(webDist, "index.html"))) {
  console.error("[package] 未找到 web/dist，请先执行: npm run build:web");
  process.exit(1);
}

const specArgs = [
  "--noconfirm",
  "--clean",
  "--onefile",
  "--name", "lite-code-backend",
  "--distpath", outDir,
  "--workpath", path.join(root, "release", "_build"),
  "--specpath", path.join(root, "release", "_spec"),
  "--paths", root,
  "--add-data", `${webDist}:web/dist`,
  "--collect-all", "fastapi",
  "--collect-all", "uvicorn",
  "--collect-all", "starlette",
  "--collect-submodules", "httpx",
  "--collect-all", "tree_sitter",
  "--collect-all", "tree_sitter_typescript",
  "--collect-submodules", "pathspec",
  "--collect-submodules", "litecode",
  "--hidden-import", "uvicorn.logging",
  "--hidden-import", "uvicorn.loops",
  "--hidden-import", "uvicorn.loops.auto",
  "--hidden-import", "uvicorn.protocols",
  "--hidden-import", "uvicorn.protocols.http",
  "--hidden-import", "uvicorn.protocols.http.auto",
  "--hidden-import", "uvicorn.protocols.websockets",
  "--hidden-import", "uvicorn.protocols.websockets.auto",
  "--hidden-import", "uvicorn.lifespan",
  "--hidden-import", "uvicorn.lifespan.on",
  path.join(root, "litecode_entry.py"),
];

console.log("[package] PyInstaller 打包后端…");
execSync([python, "-m", "PyInstaller", ...specArgs].join(" "), {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, PYTHONPATH: root },
});

const binary = path.join(outDir, "lite-code-backend");
if (!fs.existsSync(binary)) {
  console.error("[package] 后端打包失败：未生成二进制");
  process.exit(1);
}
console.log(`[package] 后端二进制: ${binary}`);
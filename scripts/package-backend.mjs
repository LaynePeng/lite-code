// Package the backend: PyInstaller produces a standalone binary (release/backend/lite-code-backend)
// Cross-platform: adapts venv paths and the --add-data separator for Windows / macOS / Linux.
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const isWindows = process.platform === "win32";
const python = isWindows
  ? path.join(root, ".venv", "Scripts", "python.exe")
  : path.join(root, ".venv", "bin", "python");
const outDir = path.join(root, "release", "backend");

if (!fs.existsSync(python)) {
  console.error(
    isWindows
      ? "[package] .venv\\Scripts\\python.exe not found. Run first: python -m venv .venv && .venv\\Scripts\\pip install -e ."
      : "[package] .venv/bin/python not found. Run first: python3 -m venv .venv && .venv/bin/pip install -e ."
  );
  process.exit(1);
}

fs.mkdirSync(outDir, { recursive: true });

const webDist = path.join(root, "web", "dist");
if (!fs.existsSync(path.join(webDist, "index.html"))) {
  console.error("[package] web/dist not found. Run first: npm run build:web");
  process.exit(1);
}

// Windows uses ';', macOS/Linux use ':'
const sep = isWindows ? ";" : ":";

const specArgs = [
  "--noconfirm",
  "--clean",
  "--onefile",
  "--name", "lite-code-backend",
  "--distpath", outDir,
  "--workpath", path.join(root, "release", "_build"),
  "--specpath", path.join(root, "release", "_spec"),
  "--paths", root,
  "--add-data", `${webDist}${sep}web${path.sep}dist`,
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

console.log("[package] Packaging backend with PyInstaller...");
console.log(`  python: ${python}`);
console.log(`  output: ${outDir}/lite-code-backend${isWindows ? ".exe" : ""}`);
console.log(`  mode: --onefile (this may take 1-3 min, mostly collecting tree-sitter etc.)`);
execSync([python, "-m", "PyInstaller", ...specArgs].join(" "), {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, PYTHONPATH: root },
});

const binary = path.join(outDir, isWindows ? "lite-code-backend.exe" : "lite-code-backend");
if (!fs.existsSync(binary)) {
  console.error("[package] Backend packaging failed: binary was not generated");
  process.exit(1);
}
console.log(`[package] Backend binary: ${binary}`);
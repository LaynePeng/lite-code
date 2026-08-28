// 完整打包：PyInstaller 后端 → electron-builder 桌面应用
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

console.log("[package] 步骤0/3: 生成应用图标");
execSync("node scripts/generate-icon.mjs", { cwd: root, stdio: "inherit" });

console.log("[package] 步骤1/3: 打包后端二进制");
execSync("node scripts/package-backend.mjs", { cwd: root, stdio: "inherit" });

console.log("[package] 步骤2/3: electron-builder 打包桌面应用");
try {
  execSync("npx electron-builder --mac", { cwd: root, stdio: "inherit" });
} catch (err) {
  console.error("[package] electron-builder 失败（可尝试: npx electron-builder --mac --dir 仅产出未签名 .app）");
  process.exit(1);
}
console.log("[package] 完成 → release/ 目录");
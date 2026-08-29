// 完整打包：PyInstaller 后端 → electron-builder --dir 产出 .app → hdiutil 手动打 DMG
// 说明：
// - electron-builder 直接打 DMG 在某些环境会产出空 .app（被 macOS 判定为损坏/恶意软件），
//   因此改为两步：先生成 .app（--dir），再用 hdiutil create 手动封装 DMG。
// - 网络受限环境（国内）需设置镜像环境变量，否则 electron-builder 下载二进制会卡住无输出。
//   用法: ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/" \
//         ELECTRON_BUILDER_BINARIES_MIRROR="https://npmmirror.com/mirrors/electron-builder-binaries/" \
//         node scripts/package.mjs
import { execSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf-8"));
const version = pkg.version;

const env = { ...process.env };

// 国内用户可预设 ELECTRON_MIRROR 等镜像环境变量，避免 electron-builder 下载卡死
// 这里给一个默认镜像兜底（仅当用户未设置时），不会覆盖已有环境变量
if (!env.ELECTRON_MIRROR && !env.ELECTRON_BUILDER_BINARIES_MIRROR) {
  env.ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/";
  env.ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/";
}

console.log("[package] 步骤0/4: 生成应用图标");
execSync("node scripts/generate-icon.mjs", { cwd: root, stdio: "inherit" });

console.log("[package] 步骤1/4: 打包后端二进制");
execSync("node scripts/package-backend.mjs", { cwd: root, stdio: "inherit" });

console.log("[package] 步骤2/4: electron-builder --dir 产出 .app");
execSync("npx electron-builder --mac --dir --publish never", { cwd: root, stdio: "inherit", env });

const appDir = path.join(root, "release", "mac-arm64", "lite-code.app");
if (!fs.existsSync(path.join(appDir, "Contents", "MacOS", "lite-code"))) {
  console.error("[package] 生成的 .app 不完整，请检查 electron-builder 输出");
  process.exit(1);
}

console.log("[package] 步骤3/4: hdiutil 手动封装 DMG");
const staging = fs.mkdtempSync(path.join(os.tmpdir(), "lc-dmg-"));
const dmgName = `lite-code-${version}-arm64.dmg`;
const dmgPath = path.join(root, "release", dmgName);

try {
  fs.copyFileSync(path.join(appDir, "Contents", "Resources", "app-icon.icns"),
    path.join(staging, ".VolumeIcon.icns"));
} catch { /* 图标可选 */ }
execSync(`cp -R "${appDir}" "${staging}/lite-code.app"`, { cwd: root, stdio: "inherit" });
fs.symlinkSync("/Applications", path.join(staging, "Applications"), "dir");

execSync(
  `hdiutil create -volname "lite-code ${version}" -srcfolder "${staging}" -ov -format UDZO "${dmgPath}"`,
  { cwd: root, stdio: "inherit" }
);

fs.rmSync(staging, { recursive: true, force: true });
console.log(`[package] 完成 → release/${dmgName}`);

// 生成 macOS 应用图标：app-icon.svg → iconset → .icns（放到 release/resources/）
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const svg = path.join(root, "scripts", "app-icon.svg");
const workDir = path.join(root, "release", "_icon");
const iconset = path.join(workDir, "app-icon.iconset");
const outDir = path.join(root, "release", "resources");
const icns = path.join(outDir, "app-icon.icns");

fs.mkdirSync(iconset, { recursive: true });
fs.mkdirSync(outDir, { recursive: true });

// 1. SVG → 1024 PNG
const bigPng = path.join(workDir, "icon-1024.png");
execSync(`sips -s format png "${svg}" -z 1024 1024 --out "${bigPng}"`, { stdio: "inherit" });

// 2. 生成各尺寸
const sizes = [
  [16, "icon_16x16.png"],
  [32, "icon_16x16@2x.png"],
  [32, "icon_32x32.png"],
  [64, "icon_32x32@2x.png"],
  [128, "icon_128x128.png"],
  [256, "icon_128x128@2x.png"],
  [256, "icon_256x256.png"],
  [512, "icon_256x256@2x.png"],
  [512, "icon_512x512.png"],
  [1024, "icon_512x512@2x.png"],
];
for (const [px, name] of sizes) {
  execSync(`sips -z ${px} ${px} "${bigPng}" --out "${path.join(iconset, name)}"`, { stdio: "inherit" });
}

// 3. iconset → icns
execSync(`iconutil -c icns "${iconset}" -o "${icns}"`, { stdio: "inherit" });
console.log(`[icon] 生成完成: ${icns}`);
fs.rmSync(workDir, { recursive: true, force: true });
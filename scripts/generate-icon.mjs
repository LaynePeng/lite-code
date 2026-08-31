// Generate the macOS app icon: app-icon.svg -> iconset -> .icns (output: release/resources/)
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const svg = path.join(root, "scripts", "app-icon.svg");
const workDir = path.join(root, "release", "_icon");
const iconset = path.join(workDir, "app-icon.iconset");
const outDir = path.join(root, "release", "resources");
const icns = path.join(outDir, "app-icon.icns");

// 清理上一次失败留下的 iconset，保证重复打包时 sips 不会覆盖残留临时文件。
fs.rmSync(workDir, { recursive: true, force: true });
fs.rmSync(icns, { force: true });
fs.mkdirSync(iconset, { recursive: true });
fs.mkdirSync(outDir, { recursive: true });

// 1. SVG -> 1024 PNG. sharp avoids sips' intermittent temporary-file rename failure.
const bigPng = path.join(workDir, "icon-1024.png");
await sharp(svg).resize(1024, 1024).png().toFile(bigPng);

// 2. Generate all sizes
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
  await sharp(bigPng).resize(px, px).png().toFile(path.join(iconset, name));
}

// 3. iconset -> icns
execSync(`iconutil -c icns "${iconset}" -o "${icns}"`, { stdio: "inherit" });
console.log(`[icon] Done: ${icns}`);
fs.rmSync(workDir, { recursive: true, force: true });

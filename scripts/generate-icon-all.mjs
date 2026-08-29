// 跨平台图标生成：app-icon.svg → PNG（sharp）→ .icns（macOS）/ .ico（Windows）/ .png（Linux）
// 依赖 sharp（SVG 渲染）与 app-builder-bin（ICNS/ICO 转换），均为 devDependencies。
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const svg = path.join(root, "scripts", "app-icon.svg");
const outDir = path.join(root, "release", "resources");
const png1024 = path.join(outDir, "app-icon-1024.png");

fs.mkdirSync(outDir, { recursive: true });

function appBuilderBin() {
  const arch = process.arch === "arm64" ? "arm64" : "amd64";
  const base = path.join(root, "node_modules", "app-builder-bin");
  const bin = process.platform === "win32"
    ? path.join(base, "win", "app-builder.exe")
    : process.platform === "darwin"
      ? path.join(base, "mac", `app-builder_${arch}`)
      : path.join(base, "linux", `app-builder_${arch}`);
  if (fs.existsSync(bin)) return bin;
  return "app-builder"; // PATH 兜底
}

async function main() {
  console.log("[icon] 1/3: SVG → 1024 PNG (sharp)");
  await sharp(svg).resize(1024, 1024).png().toFile(png1024);

  const builder = appBuilderBin();

  console.log("[icon] 2/3: 生成各平台图标");
  if (process.platform === "darwin") {
    execSync(`"${builder}" icon -i "${png1024}" -f icns --out "${outDir}"`, { stdio: "inherit" });
  } else {
    // Windows / Linux：生成 .ico（electron-builder win 配置引用）
    execSync(`"${builder}" icon -i "${png1024}" -f ico --out "${outDir}"`, { stdio: "inherit" });
  }

  // 同时保留 1024 PNG（Linux / 通用备用）
  const png = path.join(outDir, "app-icon.png");
  fs.copyFileSync(png1024, png);
  console.log("[icon] 完成:");
  for (const f of fs.readdirSync(outDir)) {
    if (f.includes("app-icon")) console.log(`   release/resources/${f}`);
  }
}

main().catch((err) => {
  console.error("[icon] 生成失败:", err.message);
  process.exit(1);
});
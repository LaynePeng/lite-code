// Cross-platform icon generation: app-icon.svg -> PNG (sharp) -> .icns (macOS) / .ico (Windows) / .png (Linux)
// Depends on sharp (SVG rendering) and app-builder-bin (ICNS/ICO conversion), both devDependencies.
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
  return "app-builder"; // PATH fallback
}

async function main() {
  console.log("[icon] 1/3: SVG -> 1024 PNG (sharp)");
  await sharp(svg).resize(1024, 1024).png().toFile(png1024);

  const builder = appBuilderBin();

  console.log("[icon] 2/3: Generate platform icons");
  if (process.platform === "darwin") {
    execSync(`"${builder}" icon -i "${png1024}" -f icns --out "${outDir}"`, { stdio: "inherit" });
  } else {
    // Windows / Linux: generate .ico (referenced by the electron-builder win config)
    execSync(`"${builder}" icon -i "${png1024}" -f ico --out "${outDir}"`, { stdio: "inherit" });
  }

  // Keep the 1024 PNG as well (Linux / general fallback)
  const png = path.join(outDir, "app-icon.png");
  fs.copyFileSync(png1024, png);
  console.log("[icon] Done:");
  for (const f of fs.readdirSync(outDir)) {
    if (f.includes("app-icon")) console.log(`   release/resources/${f}`);
  }
}

main().catch((err) => {
  console.error("[icon] Generation failed:", err.message);
  process.exit(1);
});
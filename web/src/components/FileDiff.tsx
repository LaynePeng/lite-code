// 文件修改 diff 展示（opencode 风格）：文件路径 + 增删行数徽标 + 行级 patch 着色
import type { ReactNode } from "react";

// 判定结果是否为文件修改回执（含 diff 正文）
export function isFileDiff(text: string): boolean {
  return text.includes("[Patch Success]") && text.includes("\n@@");
}

// 解析 "[Patch Success]: 已更新 <path> (+N -M)" → 文件徽标
export function DiffStats({ text }: { text: string }) {
  const m = text.match(/^\[Patch Success\]: 已更新 (.+?) \(\+(\d+) -(\d+)\)/);
  if (!m) return null;
  return (
    <div className="diff-stats">
      <span className="diff-file">📄 {m[1]}</span>
      <span className="diff-add">+{m[2]}</span>
      <span className="diff-del">−{m[3]}</span>
    </div>
  );
}

// 按行渲染 patch：+ 绿 / − 红 / @@ 高亮 / 文件头灰
export function DiffPre({ text }: { text: string }) {
  if (!isFileDiff(text)) return <pre className="tool-result">{text}</pre>;
  const lines = text.split("\n");
  const nodes: ReactNode[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const cls = line.startsWith("+++") || line.startsWith("---")
      ? "diff-meta"
      : line.startsWith("@@")
        ? "diff-hunk"
        : line.startsWith("+")
          ? "diff-add"
          : line.startsWith("-")
            ? "diff-del"
            : "";
    nodes.push(
      <span key={i} className={cls}>
        {line}
        {"\n"}
      </span>
    );
  }
  return <pre className="tool-result diff">{nodes}</pre>;
}
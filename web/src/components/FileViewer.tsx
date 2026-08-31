import { useEffect, useMemo, useState } from "react";
import type { TabItem } from "../types";

export default function FileViewer({ tab }: { tab: TabItem }) {
  const [diffOpen, setDiffOpen] = useState(true);

  const lines = useMemo(() => (tab.fileContent ?? "").split("\n"), [tab.fileContent]);

  // 解析 diff 成行级渲染
  const diffLines = useMemo(() => {
    if (!tab.fileDiff) return [];
    const result: { type: "add" | "del" | "hunk" | "meta" | "context"; text: string }[] = [];
    for (const line of tab.fileDiff.split("\n")) {
      if (line.startsWith("@@")) result.push({ type: "hunk", text: line });
      else if (line.startsWith("+") && !line.startsWith("+++")) result.push({ type: "add", text: line });
      else if (line.startsWith("-") && !line.startsWith("---")) result.push({ type: "del", text: line });
      else if (line.startsWith("---") || line.startsWith("+++")) result.push({ type: "meta", text: line });
      else if (line.trim()) result.push({ type: "context", text: line });
    }
    return result;
  }, [tab.fileDiff]);

  const hasDiff = diffLines.length > 0;

  return (
    <div className="file-viewer">
      <div className="file-viewer-header">
        <span className="file-viewer-path">{tab.filePath}</span>
        {tab.fileLanguage && <span className="file-viewer-lang">{tab.fileLanguage}</span>}
        <span className="file-viewer-lines">{lines.length} 行</span>
        {hasDiff && (
          <button className="file-viewer-diff-toggle" onClick={() => setDiffOpen(!diffOpen)}>
            {diffOpen ? "▾ 隐藏 diff" : "▸ 显示 diff（未提交改动）"}
          </button>
        )}
      </div>

      {hasDiff && diffOpen && (
        <div className="file-diff-panel">
          <div className="file-diff-header">未提交的改动</div>
          <div className="file-diff-body">
            {diffLines.map((l, i) => (
              <div key={i} className={`diff-line diff-${l.type}`}>
                <span className="diff-line-num" />
                <span className="diff-line-content">{l.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="file-content">
        <table className="file-code-table">
          <tbody>
            {lines.map((line, i) => (
              <tr key={i} className="code-line">
                <td className="code-line-num">{i + 1}</td>
                <td className="code-line-content">{line || " "}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
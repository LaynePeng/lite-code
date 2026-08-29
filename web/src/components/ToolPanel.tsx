import { useEffect, useMemo, useRef, useState } from "react";
import type { Msg, ToolCardInfo } from "../types";

function ToolIcon({ name }: { name: string }) {
  const emoji = name.startsWith("git")
    ? "𑁍"
    : name.includes("search") || name.includes("outline") || name.includes("focus")
      ? "◆"
      : name.includes("write") || name.includes("replace") || name.includes("diff") || name.includes("patch")
        ? "✎"
        : name === "execute_command"
          ? "⚡"
          : name === "spawn_sub_agent"
            ? "◈"
            : "☰";
  return <span className="tool-icon">{emoji}</span>;
}

// 从工具结果里提取一句话摘要
function summarize(result: string): string {
  const t = (result ?? "").trim();
  if (!t) return "";
  const line = t.split("\n")[0].slice(0, 60);
  if (t.startsWith("[Tool Execution Cancelled]")) return "已取消";
  if (t.startsWith("[Blocked")) return "被安全策略拦截";
  if (t.startsWith("[Execution Exception]") || t.startsWith("[Error]")) return line;
  if (t.startsWith("[Success]")) return line;
  if (t.startsWith("[Tool Timeout]")) return line;
  return line;
}

function ToolRow({ card }: { card: ToolCardInfo }) {
  const [open, setOpen] = useState(false);
  const running = card.status === "running";
  const summary = summarize(card.result ?? "");

  return (
    <div className={`tool-row ${running ? "running" : ""}`}>
      <button
        className={`tool-row-main ${open ? "open" : ""}`}
        onClick={() => setOpen(!open)}
      >
        <ToolIcon name={card.name} />
        <span className="tool-row-name">{card.name}</span>
        <span className="tool-row-summary">{summary || "…"}</span>
        {running && <span className="tool-row-spinner" />}
        <span className="tool-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="tool-row-detail">
          <div className="tool-block">
            <div className="tool-block-label">参数</div>
            <pre>{JSON.stringify(card.args, null, 2)}</pre>
          </div>
          {card.result && (
            <div className="tool-block">
              <div className="tool-block-label">结果</div>
              <pre className="tool-result">{card.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 从消息历史构建工具调用列表

interface HistoryTool {
  card: ToolCardInfo;
  result?: string;
}

function buildHistoryTools(messages: Msg[]): HistoryTool[] {
  const out: HistoryTool[] = [];
  for (const m of messages) {
    if (m.role === "assistant") {
      for (const tc of m.tool_calls ?? []) {
        out.push({
          card: {
            id: tc.id || `h-${out.length}`,
            name: tc.function.name,
            args: (() => {
              try {
                return JSON.parse(tc.function.arguments);
              } catch {
                return tc.function.arguments;
              }
            })(),
            status: "done" as const,
          },
        });
      }
    } else if (m.role === "tool") {
      const target = out.find((t) => t.card.id === m.tool_call_id);
      if (target) {
        target.result = m.content ?? "";
        const content = m.content ?? "";
        if (content.startsWith("[Tool Execution Cancelled]") || content.startsWith("[Blocked")) {
          target.card = { ...target.card, status: "cancelled" };
        } else if (content.startsWith("[Execution Exception]") || content.startsWith("[Error]")) {
          target.card = { ...target.card, status: "error" };
        }
      }
    }
  }
  return out;
}

// ---------------------------------------------------------------- 主组件

export default function ToolPanel({
  messages,
  streaming,
  running,
}: {
  messages: Msg[];
  streaming: { content: string; cards: ToolCardInfo[]; turn?: number } | null;
  running: boolean;
}) {
  const history = useMemo(() => buildHistoryTools(messages), [messages]);
  const liveCards = streaming?.cards ?? [];
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [history.length, liveCards.length, running]);

  return (
    <aside className="tool-panel">
      <div className="tool-panel-header">
        <span>工具调用</span>
        {running && <span className="tool-panel-live">● 进行中</span>}
      </div>
      <div className="tool-panel-body">
        {history.length === 0 && liveCards.length === 0 ? (
          <div className="tool-panel-empty">
            {running ? "正在调用工具…" : "暂无工具调用"}
          </div>
        ) : (
          <>
            {history.map((t, i) => (
              <ToolRow key={t.card.id || i} card={{ ...t.card, result: t.result }} />
            ))}
            {liveCards.map((c) => (
              <ToolRow key={c.id} card={c} />
            ))}
          </>
        )}
        <div ref={bottomRef} />
      </div>
    </aside>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import type { ContextStats, ContextTaskStats, Msg, ToolCardInfo } from "../types";

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

// ---------------------------------------------------------------- 上下文情况 tab

function fmt(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString();
}

function pct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

function ContextBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="ctx-row">
      <span className="ctx-row-label">{label}</span>
      <b className="ctx-row-value">{value}</b>
    </div>
  );
}

function ContextPanel({ stats }: { stats: ContextStats | null }) {
  if (!stats) {
    return <div className="tool-panel-empty">暂无上下文数据（发起对话后显示）</div>;
  }
  const window = stats.context_window || 0;
  const prompt = stats.task?.last_prompt_tokens ?? stats.task?.prompt_tokens ?? 0;
  const ratio = window > 0 ? prompt / window : 0;
  const pctWidth = Math.min(100, Math.max(0, ratio * 100));
  const danger = ratio >= 0.9;
  const task = stats.task ?? ({} as ContextTaskStats);
  const session = stats.session ?? {};

  return (
    <div className="ctx-panel">
      <div className="ctx-title" title="模型上下文窗口（models.dev 同步/内置表/手动覆盖）">
        {stats.model || "模型"} · 窗口 {window ? window.toLocaleString() : "?"} tokens
      </div>

      <div className="ctx-progress">
        <div className={`ctx-progress-track ${danger ? "danger" : ""}`}>
          <div
            className="ctx-progress-fill"
            style={{ width: `${pctWidth}%` }}
          />
        </div>
        <div className="ctx-progress-meta">
          <span>
            本次 {fmt(prompt)} · {pct(ratio)}
          </span>
          {danger && <span className="ctx-danger">≥90%，已自动压缩</span>}
        </div>
      </div>

      <div className="ctx-section-label">本次调用（模型准确返回）</div>
      <ContextBlock label="Prompt tokens" value={fmt(task.prompt_tokens)} />
      <ContextBlock label="输出 tokens" value={fmt(task.output_tokens)} />
      <ContextBlock label="Cache 命中 / 未命中" value={`${fmt(task.cache_hit_tokens)} / ${fmt(task.cache_miss_tokens)}`} />
      <ContextBlock label="Cache 命中率" value={pct(task.cache_hit_rate)} />
      <ContextBlock label="上下文压缩" value={`${task.compression_count ?? 0} 次`} />
      <ContextBlock label="累计节省 tokens" value={fmt(task.compressed_tokens)} />

      <div className="ctx-section-label">会话累计</div>
      <ContextBlock label="Prompt tokens" value={fmt(session.prompt_tokens)} />
      <ContextBlock label="输出 tokens" value={fmt(session.output_tokens)} />
      <ContextBlock label="Cache 命中率" value={pct(session.cache_hit_rate)} />
      <ContextBlock label="压缩次数 / 节省" value={`${session.compression_count ?? 0} 次 / ${fmt(session.compressed_tokens)}`} />
    </div>
  );
}

// ---------------------------------------------------------------- 主组件

export default function ToolPanel({
  messages,
  streaming,
  running,
  contextStats,
}: {
  messages: Msg[];
  streaming: { content: string; cards: ToolCardInfo[]; turn?: number } | null;
  running: boolean;
  contextStats: ContextStats | null;
}) {
  const history = useMemo(() => buildHistoryTools(messages), [messages]);
  const liveCards = streaming?.cards ?? [];
  const bottomRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<"context" | "tools">("context");

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [history.length, liveCards.length, running]);

  return (
    <aside className="tool-panel">
      <div className="tool-panel-tabs">
        <button
          className={`tool-tab ${tab === "context" ? "active" : ""}`}
          onClick={() => setTab("context")}
        >
          上下文情况
        </button>
        <button
          className={`tool-tab ${tab === "tools" ? "active" : ""}`}
          onClick={() => setTab("tools")}
        >
          工具调用{running ? " ●" : ""}
        </button>
      </div>
      <div className="tool-panel-body">
        {tab === "context" && <ContextPanel stats={contextStats} />}
        {tab === "tools" &&
          (history.length === 0 && liveCards.length === 0 ? (
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
          ))}
        <div ref={bottomRef} />
      </div>
    </aside>
  );
}
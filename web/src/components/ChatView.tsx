import { useMemo, useRef, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { DiffPre, DiffStats, isFileDiff } from "./FileDiff";
import type { Msg, ToolCardInfo, WorkItem } from "../types";

// ---------------------------------------------------------------- 渲染助手

const toolArgsOf = (m: Msg): { name: string; args: string }[] =>
  (m.tool_calls ?? []).map((tc) => ({
    name: tc.function.name,
    args: tc.function.arguments,
  }));

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
            : name === "webfetch" || name === "webfetch_batch"
              ? "➤"
              : "☰";
  return <span className="tool-icon">{emoji}</span>;
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} rehypePlugins={[rehypeHighlight]}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

// ---------------------------------------------------------------- 工具卡片

function ToolCard({ card }: { card: ToolCardInfo }) {
  const important = isFileDiff(card.result ?? "") || card.status === "error" || card.status === "cancelled";
  // 只有 diff / 异常默认展开；普通调用保持为紧凑的一行
  const [open, setOpen] = useState(() => important);

  useEffect(() => {
    if (isFileDiff(card.result ?? "") || card.status === "error" || card.status === "cancelled") setOpen(true);
  }, [card.result, card.status]);

  const statusClass = card.status === "running" ? "running" : card.status;
  const argsPreview = typeof card.args === "string"
    ? card.args
    : JSON.stringify(card.args) ?? "";

  return (
    <div className={`tool-card ${statusClass} ${important ? "tool-card-important" : "tool-card-compact"}`}>
      <button className="tool-card-header" onClick={() => setOpen(!open)}>
        <ToolIcon name={card.name} />
        <span className="tool-name">{card.name}</span>
        <span className="tool-args-preview">{argsPreview.slice(0, 120)}</span>
        {card.status === "running" && <span className="tool-status running">运行中…</span>}
        {card.status === "done" && <span className="tool-status done">✓ {card.durationMs !== undefined ? `${card.durationMs}ms` : "完成"}</span>}
        {card.status === "cancelled" && <span className="tool-status cancelled">已取消</span>}
        {card.status === "error" && <span className="tool-status error">执行异常</span>}
        <span className="tool-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="tool-card-body">
          <div className="tool-block">
            <div className="tool-block-label">参数</div>
            <pre>{JSON.stringify(card.args, null, 2)}</pre>
          </div>
          {card.result && <div className="tool-block"><div className="tool-block-label">结果</div><DiffStats text={card.result} /><DiffPre text={card.result} /></div>}
        </div>
      )}
    </div>
  );
}

function ToolActivity({ tools }: { tools: ToolCardInfo[] }) {
  return (
    <div className="tool-activity" title={tools.map((tool) => tool.name).join(" · ")}>
      <span className="tool-activity-label">工具</span>
      {tools.slice(-8).map((tool) => (
        <span className={`tool-activity-item ${tool.status}`} key={tool.id}>
          <ToolIcon name={tool.name} />
          <span>{tool.name}</span>
          <span className="tool-activity-state">
            {tool.status === "running" ? "…" : tool.status === "done" ? "✓" : "!"}
          </span>
        </span>
      ))}
      {tools.length > 8 && <span className="tool-activity-more">+{tools.length - 8}</span>}
    </div>
  );
}

function WorkItems({ items, streaming = false }: { items: WorkItem[]; streaming?: boolean }) {
  return (
    <div className="work-timeline">
      {items.map((item) => {
        if (item.type === "text") {
          return (
            <div className="msg-row assistant timeline-text" key={item.id}>
              <div className="assistant-avatar">⚡</div>
              <div className="bubble assistant-bubble">
                <Markdown text={item.content} />
                {streaming && item.id === items[items.length - 1]?.id && <span className="cursor"><span /></span>}
              </div>
            </div>
          );
        }
        if (item.type === "activity") {
          const important = item.tools.filter((tool) =>
            isFileDiff(tool.result ?? "") || tool.status === "error" || tool.status === "cancelled"
          );
          const compact = item.tools.filter((tool) => !important.includes(tool));
          return (
            <div className="work-item-group" key={item.id}>
              {compact.length > 0 && <ToolActivity tools={compact} />}
              {important.map((tool) => <ToolCard key={tool.id} card={tool} />)}
            </div>
          );
        }
        return <ToolCard key={item.id} card={item.card} />;
      })}
    </div>
  );
}

// ---------------------------------------------------------------- 消息气泡

function MessageBubble({ message }: { message: Msg }) {
  if (message.role === "user") {
    return (
      <div className="msg-row user">
        <div className="bubble user-bubble">
          <Markdown text={message.content ?? ""} />
        </div>
      </div>
    );
  }
  return (
    <div className="msg-row assistant">
      <div className="assistant-avatar">⚡</div>
      <div className="bubble assistant-bubble">
        {message.content && <Markdown text={message.content} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 正在生成

function StreamingTurn({ items, turn }: { items: WorkItem[]; turn?: number }) {
  return (
    <>
      {items.length === 0 && <div className="timeline-thinking"><span className="dot" /><span className="dot" /><span className="dot" /><span className="thinking-text">思考中…{turn ? ` (第 ${turn} 轮)` : ""}</span></div>}
      <WorkItems items={items} streaming />
    </>
  );
}

// ---------------------------------------------------------------- 历史消息分组

interface RenderTurn {
  key: string;
  user?: Msg;
  items: WorkItem[];
  assistant?: Msg;
}

function parseToolArgs(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function buildTurns(messages: Msg[]): RenderTurn[] {
  const turns: RenderTurn[] = [];
  let current: RenderTurn | null = null;
  const pending = new Map<string, ToolCardInfo>();

  for (const m of messages) {
    if (m.role === "user") {
      current = null;
      turns.push({ key: `u-${turns.length}`, user: m, items: [] });
    } else if (m.role === "assistant") {
      const hasTools = (m.tool_calls ?? []).length > 0;
      if (!hasTools) {
        current = null;
        turns.push({ key: `a-${turns.length}`, items: [], assistant: m });
        continue;
      }
      if (!current) {
        current = { key: `t-${turns.length}`, items: [] };
        turns.push(current);
      }
      if (m.content) current.items.push({ type: "text", id: `text-${turns.length}-${current.items.length}`, content: m.content });
      for (const tc of m.tool_calls ?? []) {
        const card: ToolCardInfo = {
          id: tc.id || `h-${current.items.length}-${Math.random().toString(36).slice(2, 6)}`,
          name: tc.function.name,
          args: parseToolArgs(tc.function.arguments),
          status: "done",
        };
        pending.set(card.id, card);
        current.items.push({ type: "tool", id: card.id, card });
      }
    } else if (m.role === "tool") {
      const target = m.tool_call_id ? pending.get(m.tool_call_id) : undefined;
      if (!target) continue;
      target.result = m.content ?? "";
      const content = m.content ?? "";
      if (content.startsWith("[Tool Execution Cancelled]") || content.startsWith("[Blocked")) target.status = "cancelled";
      else if (content.startsWith("[Execution Exception]") || content.startsWith("[Error]")) target.status = "error";
    }
  }
  return turns;
}

// ---------------------------------------------------------------- 主组件

export default function ChatView({
  sessionId,
  sessionTitle,
  messages,
  streaming,
  running,
  turn,
  pendingApproval,
  onSend,
  onStop,
  onApprove,
}: {
  sessionId: string;
  sessionTitle: string;
  messages: Msg[];
  streaming: { items: WorkItem[]; turn?: number } | null;
  running: boolean;
  turn: number;
  pendingApproval: { id: string; action: string; reason: string } | null;
  onSend: (prompt: string) => void;
  onStop: () => void;
  onApprove: (approved: boolean) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const turns = useMemo(() => buildTurns(messages), [messages]);

  // 用户手动上翻浏览历史时暂停自动跟随；滚回底部附近自动恢复
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!stickRef.current) return;
    // 流式期间瞬时定位（smooth 动画追不上高频内容增长会产生抖动）
    bottomRef.current?.scrollIntoView({
      behavior: streaming ? "auto" : "smooth",
      block: "end",
    });
  }, [messages, streaming, turns.length]);

  return (
    <div className="chat-view">
      <div className="chat-scroll" ref={scrollRef}>
        {turns.length === 0 && !streaming ? (
          <div className="empty-state">
            <div className="empty-logo">⚡</div>
            <h2>lite-code</h2>
            <p>手写内核的 Code 开发 Agent，已就绪。</p>
            <div className="empty-hints">
              <button onClick={() => onSend("帮我查看这个项目的代码结构")}>🔍 查看项目结构</button>
              <button onClick={() => onSend("帮我搜索代码里所有 TODO 标记并分析")}>🕵️ 搜索 TODO 并分析</button>
              <button onClick={() => onSend("运行测试并总结结果")}>🧪 运行测试并总结</button>
              <button onClick={() => onSend("审查当前未提交的代码改动")}>🔎 审查代码改动</button>
            </div>
          </div>
        ) : (
          <>
            <div className="session-badge">{sessionTitle}</div>
            {turns.map((t) => (
              <div key={t.key}>
                {t.user && <MessageBubble message={t.user} />}
                {t.items.length > 0 && (
                  <WorkItems items={t.items} />
                )}
                {t.assistant && t.assistant.content && (
                  <div className="msg-row assistant">
                    <div className="assistant-avatar">⚡</div>
                    <div className="bubble assistant-bubble">
                      <Markdown text={t.assistant.content} />
                    </div>
                  </div>
                )}
              </div>
            ))}
            {streaming && (
              <StreamingTurn items={streaming.items} turn={streaming.turn} />
            )}
          </>
        )}
        <div ref={bottomRef} />
      </div>

      {pendingApproval && (
        <div className="approval-overlay">
          <div className="approval-card">
            <div className="approval-icon">🛡️</div>
            <h3>需要你的确认</h3>
            <p className="approval-action">{pendingApproval.action}</p>
            <p className="approval-reason">{pendingApproval.reason}</p>
            <div className="approval-buttons">
              <button className="btn-deny" onClick={() => onApprove(false)}>
                拒绝
              </button>
              <button className="btn-approve" onClick={() => onApprove(true)}>
                允许执行
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// 导出供 App 使用
export { ToolCard, Markdown };
export type { RenderTurn };

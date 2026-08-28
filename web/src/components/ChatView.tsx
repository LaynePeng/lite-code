import { useMemo, useRef, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Msg, ToolCardInfo } from "../types";

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
            : "☰";
  return <span className="tool-icon">{emoji}</span>;
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

// ---------------------------------------------------------------- 工具卡片

function ToolCard({ card }: { card: ToolCardInfo }) {
  const [open, setOpen] = useState(false);
  const statusClass =
    card.status === "running" ? "running" : card.status === "done" ? "done" : "cancelled";

  return (
    <div className={`tool-card ${statusClass}`}>
      <button className="tool-card-header" onClick={() => setOpen(!open)}>
        <ToolIcon name={card.name} />
        <span className="tool-name">{card.name}</span>
        <span className="tool-args-preview">
          {typeof card.args === "string" ? card.args.slice(0, 80) : JSON.stringify(card.args).slice(0, 80)}
        </span>
        {card.status === "running" && <span className="tool-status running">运行中…</span>}
        {card.status === "done" && (
          <span className="tool-status done">
            ✓ {card.durationMs !== undefined ? `${card.durationMs}ms` : "完成"}
          </span>
        )}
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

function StreamingTurn({
  content,
  cards,
  turn,
}: {
  content: string;
  cards: ToolCardInfo[];
  turn?: number;
}) {
  return (
    <div className="msg-row assistant">
      <div className="assistant-avatar">⚡</div>
      <div className="bubble assistant-bubble streaming">
        {cards.length > 0 && (
          <div className="tool-cards">
            {cards.map((c) => (
              <ToolCard key={c.id} card={c} />
            ))}
          </div>
        )}
        {content && <Markdown text={content} />}
        {!content && cards.length === 0 && (
          <div className="thinking">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
            <span className="thinking-text">思考中…{turn ? ` (第 ${turn} 轮)` : ""}</span>
          </div>
        )}
        {content && (
          <span className="cursor-bar">
            <span className="cursor" />
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 历史消息分组

interface RenderTurn {
  key: string;
  assistant: Msg | null;
  tools: { card: ToolCardInfo; result?: string }[];
  user?: Msg;
}

function buildTurns(messages: Msg[]): RenderTurn[] {
  const turns: RenderTurn[] = [];
  let current: RenderTurn | null = null;

  for (const m of messages) {
    if (m.role === "user") {
      current = null;
      turns.push({ key: `u-${turns.length}`, assistant: null, tools: [], user: m });
    } else if (m.role === "assistant") {
      current = {
        key: `a-${turns.length}`,
        assistant: m,
        tools: (m.tool_calls ?? []).map((tc, i) => ({
          card: {
            id: tc.id || `h-${i}`,
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
        })),
      };
      turns.push(current);
    } else if (m.role === "tool" && current) {
      const target = current.tools.find((t) => t.card.id === m.tool_call_id);
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
  return turns;
}

// ---------------------------------------------------------------- 主组件

export default function ChatView({
  sessionId,
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
  messages: Msg[];
  streaming: { content: string; cards: ToolCardInfo[]; turn?: number } | null;
  running: boolean;
  turn: number;
  pendingApproval: { id: string; action: string; reason: string } | null;
  onSend: (prompt: string) => void;
  onStop: () => void;
  onApprove: (approved: boolean) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const turns = useMemo(() => buildTurns(messages), [messages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
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
            <div className="session-badge">会话 {sessionId}</div>
            {turns.map((t) => (
              <div key={t.key}>
                {t.user && <MessageBubble message={t.user} />}
                {t.assistant && (
                  <div className="msg-row assistant">
                    <div className="assistant-avatar">⚡</div>
                    <div className="bubble assistant-bubble">
                      {t.tools.length > 0 && (
                        <div className="tool-cards">
                          {t.tools.map((x) => (
                            <ToolCard key={x.card.id} card={{ ...x.card, result: x.result }} />
                          ))}
                        </div>
                      )}
                      {t.assistant.content && <Markdown text={t.assistant.content} />}
                    </div>
                  </div>
                )}
              </div>
            ))}
            {streaming && (
              <StreamingTurn
                content={streaming.content}
                cards={streaming.cards}
                turn={streaming.turn}
              />
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

      {running && (
        <div className="stop-bar">
          <button className="btn-stop" onClick={onStop}>
            ■ 停止
          </button>
        </div>
      )}
    </div>
  );
}

// 导出供 App 使用
export { ToolCard, Markdown };
export type { RenderTurn };
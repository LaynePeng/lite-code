import { useMemo, useRef, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
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
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} rehypePlugins={[rehypeHighlight]}>
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
        {!content && (
          <div className="thinking">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
            <span className="thinking-text">思考中…{turn ? ` (第 ${turn} 轮)` : ""}</span>
          </div>
        )}
        {content && <Markdown text={content} />}
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
  user?: Msg;
  thinking: string; // 该任务下累积的所有中间推理文本
  toolCount: number; // 该任务下的工具调用次数
  assistant?: Msg; // 最终回答
}

function buildTurns(messages: Msg[]): RenderTurn[] {
  const turns: RenderTurn[] = [];
  let current: RenderTurn | null = null;

  for (const m of messages) {
    if (m.role === "user") {
      current = null;
      turns.push({ key: `u-${turns.length}`, user: m, thinking: "", toolCount: 0 });
    } else if (m.role === "assistant") {
      const hasTools = (m.tool_calls ?? []).length > 0;
      if (hasTools) {
        // 中间推理轮次：累积进当前任务的 thinking 块
        if (!current) {
          current = { key: `t-${turns.length}`, thinking: "", toolCount: 0 };
          turns.push(current);
        }
        current.toolCount += (m.tool_calls ?? []).length;
        if (m.content) current.thinking += (current.thinking ? "\n\n" : "") + m.content;
      } else {
        // 最终回答：作为独立气泡
        current = null;
        turns.push({ key: `a-${turns.length}`, thinking: "", toolCount: 0, assistant: m });
      }
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
                {t.thinking && (
                  <details className="thinking-row" open={false}>
                    <summary>
                      思考过程
                      {t.thinking.replace(/\n/g, " ").slice(0, 50)}
                      {t.thinking.length > 50 ? "…" : ""}
                    </summary>
                    <div className="thinking-content">
                      <Markdown text={t.thinking} />
                    </div>
                  </details>
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
    </div>
  );
}

// 导出供 App 使用
export { ToolCard, Markdown };
export type { RenderTurn };
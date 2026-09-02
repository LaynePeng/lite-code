import { useState } from "react";
import type { ContextStats, ContextTaskStats, MCPServerStatus } from "../types";

// ---------------------------------------------------------------- 上下文情况面板

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
      <ContextBlock label="工具调用" value={`${task.tool_calls ?? 0} 次`} />
      <ContextBlock label="安全拦截" value={`${task.blocked ?? 0} 次`} />
      <ContextBlock label="预估成本" value={task.cost_estimate != null ? `¥${task.cost_estimate.toFixed(4)}` : "—"} />

      <div className="ctx-section-label">会话累计</div>
      <ContextBlock label="Prompt tokens" value={fmt(session.prompt_tokens)} />
      <ContextBlock label="输出 tokens" value={fmt(session.output_tokens)} />
      <ContextBlock label="Cache 命中率" value={pct(session.cache_hit_rate)} />
      <ContextBlock label="压缩次数 / 节省" value={`${session.compression_count ?? 0} 次 / ${fmt(session.compressed_tokens)}`} />
      <ContextBlock label="工具调用" value={`${session.tool_calls ?? 0} 次`} />
      <ContextBlock label="安全拦截" value={`${session.blocked ?? 0} 次`} />
      <ContextBlock label="预估成本" value={session.cost_estimate != null ? `¥${session.cost_estimate.toFixed(4)}` : "—"} />
    </div>
  );
}

// ---------------------------------------------------------------- MCP 状态面板

function McpPanel({ servers }: { servers: MCPServerStatus[] }) {
  if (!servers || servers.length === 0) {
    return <div className="tool-panel-empty">暂未配置 MCP</div>;
  }
  return (
    <div className="mcp-status-list">
      {servers.map((s) => (
        <div className="mcp-status-item" key={s.name} title={s.error || s.tools.join(", ")}>
          <span className={`mcp-dot ${s.connected ? "on" : "off"}`} />
          <div className="mcp-status-main">
            <div className="mcp-status-name">{s.name}</div>
            <div className="mcp-status-meta">
              {s.connected
                ? `${s.tools.length} 个工具`
                : s.enabled
                  ? (s.error ? "连接失败" : "未连接")
                  : "已禁用"}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 工具面板

function ToolsPanel({ tools }: { tools: { name: string; description: string }[] }) {
  if (!tools || tools.length === 0) {
    return <div className="tool-panel-empty">暂无已注册工具（发起对话后显示）</div>;
  }
  return (
    <div className="tools-panel">
      <div className="tools-panel-count">已注册工具（{tools.length}）</div>
      <ul className="tools-list">
        {tools.map((t) => (
          <li key={t.name} title={t.description}>
            <span className="tool-dot" />
            {t.name}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- 主组件

export default function ToolPanel({ contextStats, mcpServers, tools }: { contextStats: ContextStats | null; mcpServers: MCPServerStatus[]; tools: { name: string; description: string }[] }) {
  const [panelTab, setPanelTab] = useState<"context" | "mcp" | "tools">("context");

  return (
    <aside className="tool-panel">
      <div className="panel-tabs">
        <button
          className={`panel-tab ${panelTab === "context" ? "active" : ""}`}
          onClick={() => setPanelTab("context")}
        >
          上下文
        </button>
        <button
          className={`panel-tab ${panelTab === "mcp" ? "active" : ""}`}
          onClick={() => setPanelTab("mcp")}
        >
          MCP
        </button>
        <button
          className={`panel-tab ${panelTab === "tools" ? "active" : ""}`}
          onClick={() => setPanelTab("tools")}
        >
          工具
        </button>
      </div>
      <div className="tool-panel-body tool-panel-context">
        {panelTab === "context"
          ? <ContextPanel stats={contextStats} />
          : panelTab === "mcp"
            ? <McpPanel servers={mcpServers} />
            : <ToolsPanel tools={tools} />}
      </div>
    </aside>
  );
}

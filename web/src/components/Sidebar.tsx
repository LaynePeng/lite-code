import { useEffect, useState } from "react";
import { api } from "../api";
import type { SessionInfo } from "../types";

export default function Sidebar({
  sessions,
  activeSessionId,
  workspace,
  stats,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onOpenProject,
  onOpenSettings,
}: {
  sessions: SessionInfo[];
  activeSessionId: string | null;
  workspace: string;
  stats: { input_tokens: number; output_tokens: number; cost_estimate: number; tool_calls: number; blocked: number } | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  onOpenProject: () => void;
  onOpenSettings: () => void;
}) {
  const [tab, setTab] = useState<"sessions" | "files" | "stats">("sessions");
  const [tree, setTree] = useState<string>("");
  const [tools, setTools] = useState<{ name: string; description: string }[]>([]);
  const [loadingTree, setLoadingTree] = useState(false);

  useEffect(() => {
    if (tab === "files") {
      setLoadingTree(true);
      api
        .workspaceTree(4)
        .then((r) => setTree(r.tree))
        .catch(() => setTree("（无法读取工作区）"))
        .finally(() => setLoadingTree(false));
    }
    if (tab === "stats") {
      api.tools().then(setTools).catch(() => {});
    }
  }, [tab]);

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="brand-logo">⚡</span>
        <span className="brand-name">lite-code</span>
      </div>

      <div className="sidebar-tabs">
        <button className={tab === "sessions" ? "active" : ""} onClick={() => setTab("sessions")}>
          会话
        </button>
        <button className={tab === "files" ? "active" : ""} onClick={() => setTab("files")}>
          文件
        </button>
        <button className={tab === "stats" ? "active" : ""} onClick={() => setTab("stats")}>
          统计
        </button>
      </div>

      <div className="sidebar-body">
        {tab === "sessions" && (
          <div className="sessions-list">
            <button className="btn-new-session" onClick={onNewSession}>
              ＋ 新建会话
            </button>
            {window.liteCode && (
              <button className="btn-open-session" onClick={onOpenProject} title="选择其他项目目录，切换工作区">
                📂 打开项目…
              </button>
            )}
            {sessions.length === 0 && <div className="sidebar-empty">还没有会话</div>}
            {sessions.map((s) => (
              <div
                key={s.session_id}
                className={`session-item ${s.session_id === activeSessionId ? "active" : ""}`}
                onClick={() => onSelectSession(s.session_id)}
              >
                <div className="session-title">{s.title}</div>
                <div className="session-meta">
                  {s.message_count} 条消息
                  <button
                    className="session-delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`删除会话「${s.title}」？`)) onDeleteSession(s.session_id);
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === "files" && (
          <div className="files-panel">
            <div className="files-workspace" title={workspace}>
              📁 {workspace}
            </div>
            {loadingTree ? (
              <div className="sidebar-empty">加载中…</div>
            ) : (
              <pre className="file-tree">{tree}</pre>
            )}
          </div>
        )}

        {tab === "stats" && (
          <div className="stats-panel">
            <div className="stat-block">
              <div className="stat-row">
                <span>输入 Tokens</span>
                <b>{stats ? stats.input_tokens.toLocaleString() : "—"}</b>
              </div>
              <div className="stat-row">
                <span>输出 Tokens</span>
                <b>{stats ? stats.output_tokens.toLocaleString() : "—"}</b>
              </div>
              <div className="stat-row">
                <span>工具调用</span>
                <b>{stats ? stats.tool_calls : "—"}</b>
              </div>
              <div className="stat-row">
                <span>安全拦截</span>
                <b>{stats ? stats.blocked : "—"}</b>
              </div>
              <div className="stat-row cost">
                <span>预估成本</span>
                <b>¥{stats ? stats.cost_estimate.toFixed(4) : "—"}</b>
              </div>
            </div>
            <div className="stat-divider" />
            <div className="stat-title">已注册工具（{tools.length}）</div>
            <ul className="tools-list">
              {tools.map((t) => (
                <li key={t.name} title={t.description}>
                  <span className="tool-dot" />
                  {t.name}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="sidebar-footer">
        <button className="btn-open-settings" onClick={onOpenSettings}>
          ⚙️ LLM 设置
        </button>
        <div className="footer-version">lite-code v0.1 · 手写 Agent Harness</div>
      </div>
    </aside>
  );
}

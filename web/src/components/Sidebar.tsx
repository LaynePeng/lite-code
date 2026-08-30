import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import type { SessionInfo, TreeEntry } from "../types";

// ---------------------------------------------------------------- 目录树

function FileTree({ workspace, revision }: { workspace: string; revision: number }) {
  const [dirs, setDirs] = useState<Map<string, TreeEntry[]>>(new Map());
  const [open, setOpen] = useState<Set<string>>(new Set([""]));
  const [branch, setBranch] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const openRef = useRef<Set<string>>(new Set([""]));
  const refreshingRef = useRef(false);

  const loadDir = useCallback(async (path: string) => {
    const r = await api.workspaceTree(path);
    setBranch(r.git.branch);
    setDirs((prev) => new Map(prev).set(path, r.entries));
    return r;
  }, []);

  const refresh = useCallback(async () => {
    if (refreshingRef.current) return;
    refreshingRef.current = true;
    setLoading(true);
    try {
      await Promise.all([...openRef.current].map((p) => loadDir(p)));
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
      refreshingRef.current = false;
    }
  }, [loadDir]);

  // 首次加载根目录
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 动态刷新：写工具执行 / 任务结束 / 子 Agent 完成后由 App 递增 revision
  useEffect(() => {
    if (revision > 0) void refresh();
  }, [revision, refresh]);

  const toggleDir = useCallback(
    async (path: string) => {
      const cur = openRef.current;
      if (cur.has(path)) {
        cur.delete(path);
        setOpen(new Set(cur));
        return;
      }
      if (!dirs.has(path)) {
        try {
          await loadDir(path);
        } catch {
          setError(true);
          return;
        }
      }
      cur.add(path);
      setOpen(new Set(cur));
    },
    [dirs, loadDir]
  );

  const renderNodes = (path: string, depth: number): ReactNode[] => {
    const nodes = dirs.get(path) ?? [];
    return nodes.map((n) =>
      n.type === "dir" ? (
        <div key={n.path}>
          <div
            className={`tree-row dir ${open.has(n.path) ? "open" : ""}`}
            style={{ paddingLeft: depth * 14 + 8 }}
            title={n.path}
            onClick={() => void toggleDir(n.path)}
          >
            <span className="tree-caret">{open.has(n.path) ? "▾" : "▸"}</span>
            <span className="tree-icon">📁</span>
            <span className="tree-name">{n.name}</span>
            {n.has_changes && <span className="tree-dot" title="包含改动" />}
          </div>
          {open.has(n.path) && renderNodes(n.path, depth + 1)}
        </div>
      ) : (
        <div
          key={n.path}
          className={`tree-row file ${n.status ? `st-${n.status}` : ""}`}
          style={{ paddingLeft: depth * 14 + 8 }}
          title={n.path}
        >
          <span className="tree-caret-placeholder" />
          <span className="tree-icon">{n.status === "D" ? "✕" : "📄"}</span>
          <span className="tree-name">{n.name}</span>
          {n.status && <span className="tree-status">{n.status}</span>}
        </div>
      )
    );
  };

  return (
    <div className="files-panel">
      <div className="files-header">
        <span className="files-workspace" title={workspace}>
          📁 {workspace}
        </span>
        <div className="files-header-actions">
          {branch && (
            <span className="git-branch" title="当前分支">
              ⎇ {branch}
            </span>
          )}
          <button
            className={`btn-refresh-tree ${loading ? "spinning" : ""}`}
            onClick={() => void refresh()}
            title="刷新目录树"
          >
            ↻
          </button>
        </div>
      </div>
      {loading && dirs.size === 0 ? (
        <div className="sidebar-empty">加载中…</div>
      ) : error ? (
        <div className="sidebar-empty">（无法读取工作区）</div>
      ) : (
        <div className="file-tree">{renderNodes("", 0)}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 侧边栏

export default function Sidebar({
  sessions,
  activeSessionId,
  workspace,
  stats,
  tab,
  treeRevision,
  onTabChange,
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
  tab: "sessions" | "files" | "stats";
  treeRevision: number;
  onTabChange: (tab: "sessions" | "files" | "stats") => void;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  onOpenProject: () => void;
  onOpenSettings: () => void;
}) {
  const [tools, setTools] = useState<{ name: string; description: string }[]>([]);

  useEffect(() => {
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
        <button className={tab === "sessions" ? "active" : ""} onClick={() => onTabChange("sessions")}>
          会话
        </button>
        <button className={tab === "files" ? "active" : ""} onClick={() => onTabChange("files")}>
          文件
        </button>
        <button className={tab === "stats" ? "active" : ""} onClick={() => onTabChange("stats")}>
          统计
        </button>
      </div>

      <div className="sidebar-body">
        {tab === "sessions" && (
          <div className="sessions-list">
            <button className="btn-new-session" onClick={onNewSession}>
              ＋ 新建会话
            </button>
            <button className="btn-open-session" onClick={onOpenProject} title="选择其他项目目录，切换工作区">
              📂 打开项目…
            </button>
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

        {tab === "files" && <FileTree workspace={workspace} revision={treeRevision} />}

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
        <div className="footer-version">lite-code v0.6.3-rc · 手写 Agent Harness</div>
      </div>
    </aside>
  );
}
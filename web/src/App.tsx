import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import ChatView from "./components/ChatView";
import Composer from "./components/Composer";
import FileViewer from "./components/FileViewer";
import ProjectPicker from "./components/ProjectPicker";
import SettingsModal from "./components/SettingsModal";
import Sidebar from "./components/Sidebar";
import TabBar from "./components/TabBar";
import ToolPanel from "./components/ToolPanel";
import type { AgentInfo, AppConfig, ContextStats, ChatSessionState, Msg, ServerStatus, SessionInfo, Stats, TabItem, ToolCardInfo } from "./types";

interface PendingApproval {
  id: string;
  action: string;
  reason: string;
}

interface StreamingState {
  content: string;
  cards: ToolCardInfo[];
  turn?: number;
}

const TREE_TOUCH_TOOLS = new Set([
  "write_file",
  "apply_search_replace",
  "apply_unified_diff",
  "execute_command",
  "git_commit",
]);

let tabSeq = 0;
const nextTabId = () => `tab_${++tabSeq}`;

const EMPTY_CHAT: ChatSessionState = {
  messages: [],
  streaming: null,
  running: false,
  turn: 0,
  stats: null,
  contextStats: null,
  error: null,
  pendingApproval: null,
  stalled: false,
};

export default function App() {
  const [tabs, setTabs] = useState<TabItem[]>([]);
  const [activeTabId, setActiveTabId] = useState<string>("");
  const [chatStates, setChatStates] = useState<Record<string, ChatSessionState>>({});

  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<"sessions" | "files" | "stats">("sessions");
  const [loading, setLoading] = useState(true);
  const [showDebug, setShowDebug] = useState(false);
  const [debugLogs, setDebugLogs] = useState<string[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [currentAgent, setCurrentAgent] = useState<string>("build");
  const [success, setSuccess] = useState<string | null>(null);
  const [treeRevision, setTreeRevision] = useState(0);

  const esRef = useRef<EventSource | null>(null);
  const taskIdRef = useRef<string | null>(null);
  const stopRequestedRef = useRef(false);
  const streamingRef = useRef<StreamingState | null>(null);
  const lastEventTimeRef = useRef<number>(Date.now());
  const flushTimerRef = useRef<number | null>(null);
  const chatStatesRef = useRef<Record<string, ChatSessionState>>({});
  const tabsRef = useRef<TabItem[]>([]);
  const sessionsRequestRef = useRef(0);

  useEffect(() => { chatStatesRef.current = chatStates; }, [chatStates]);
  useEffect(() => { tabsRef.current = tabs; }, [tabs]);

  const pushLog = useCallback((msg: string) => {
    const line = `${new Date().toLocaleTimeString()} ${msg}`;
    setDebugLogs((prev) => [...prev.slice(-200), line]);
  }, []);

  // ------------------------------------------------------------ 会话状态读写

  const getChat = useCallback((sid: string): ChatSessionState => {
    return chatStatesRef.current[sid] ?? EMPTY_CHAT;
  }, []);

  const patchChat = useCallback((sid: string, patch: Partial<ChatSessionState>) => {
    setChatStates((prev) => {
      const next = { ...prev, [sid]: { ...(prev[sid] ?? EMPTY_CHAT), ...patch } };
      chatStatesRef.current = next;
      return next;
    });
  }, []);

  // ------------------------------------------------------------ 当前活跃 Tab

  const activeTab = useMemo(
    () => tabs.find((t) => t.id === activeTabId) ?? null,
    [tabs, activeTabId]
  );
  const activeSessionId = activeTab?.kind === "chat" ? (activeTab.sessionId ?? null) : null;

  // 活跃会话的工作副本（读写都走 ref 缓存，避免竞态）
  const currentChat = activeSessionId ? getChat(activeSessionId) : EMPTY_CHAT;

  const patchActiveChat = useCallback(
    (patch: Partial<ChatSessionState>) => {
      const sid = activeSessionId;
      if (sid) patchChat(sid, patch);
    },
    [activeSessionId, patchChat]
  );

  // ------------------------------------------------------------ 会话列表

  const refreshSessions = useCallback(async (ws?: string) => {
    const requestId = ++sessionsRequestRef.current;
    try {
      const next = await api.sessions(ws ?? status?.workspace);
      // 多个任务结束/切换项目时请求可能乱序返回，旧响应不能覆盖新列表。
      if (requestId === sessionsRequestRef.current) setSessions(next);
    } catch {
      /* ignore */
    }
  }, [status?.workspace]);

  const refreshAll = useCallback(async () => {
    try {
      const [st, cfg, ag] = await Promise.all([api.status(), api.config(), api.agents()]);
      setStatus(st);
      setConfig(cfg);
      setAgents(ag);
      await refreshSessions(st.workspace); // 用刚取到的 workspace，避免 setState 异步时序
    } catch (e) {
      setErrorPublic((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [refreshSessions]);

  function setErrorPublic(msg: string | null) {
    patchActiveChat({ error: msg });
  }

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  // Tab 键切换 agent
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      e.preventDefault();
      setCurrentAgent((prev) => {
        const primary = agents.filter((a) => a.mode !== "subagent");
        if (primary.length < 2) return prev;
        const idx = primary.findIndex((a) => a.id === prev);
        const next = idx < 0 || idx >= primary.length - 1 ? primary[0] : primary[idx + 1];
        return next.id;
      });
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [agents]);

  // ------------------------------------------------------------ Tab 操作

  const closeStream = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    taskIdRef.current = null;
  }, []);

  const openSessionTab = useCallback(
    (sid: string, title?: string) => {
      setTabs((prev) => {
        const existing = prev.find((t) => t.kind === "chat" && t.sessionId === sid);
        if (existing) {
          setActiveTabId(existing.id);
          return prev.map((t) => t.id === existing.id ? { ...t, title: title || t.title } : t);
        }
        const tab: TabItem = { id: nextTabId(), kind: "chat", sessionId: sid, title: title || sid };
        setActiveTabId(tab.id);
        return [...prev, tab];
      });
    },
    []
  );

  const openFileTab = useCallback(async (filePath: string) => {
    closeStream();
    let content = "", diff = "", language = "";
    try {
      const r = await api.readFile(filePath);
      content = r.content;
      language = r.language;
      diff = r.diff;
    } catch (e) {
      content = `// 无法读取文件：${(e as Error).message}`;
    }
    setTabs((prev) => {
      const existing = prev.find((t) => t.kind === "file" && t.filePath === filePath);
      if (existing) {
        setActiveTabId(existing.id);
        return prev.map((t) =>
          t.id === existing.id ? { ...t, fileContent: content, fileDiff: diff, fileLanguage: language } : t
        );
      }
      const tab: TabItem = {
        id: nextTabId(), kind: "file", title: filePath.split("/").pop() ?? filePath,
        filePath, fileContent: content, fileDiff: diff, fileLanguage: language,
      };
      setActiveTabId(tab.id);
      return [...prev, tab];
    });
    setSidebarTab("files");
  }, [closeStream]);

  const closeTab = useCallback(
    (id: string) => {
      setTabs((prev) => {
        if (prev.length <= 1) return prev; // 至少保留一个
        const idx = prev.findIndex((t) => t.id === id);
        if (idx < 0) return prev;
        const next = prev.filter((t) => t.id !== id);
        if (activeTabId === id) {
          const neighbor = next[Math.min(idx, next.length - 1)];
          setActiveTabId(neighbor.id);
        }
        return next;
      });
    },
    [activeTabId]
  );

  const newChatTab = useCallback(() => {
    // 占位 tab：不创建后端 session，首条消息发送时才创建并绑定（见 send）
    closeStream();
    setTabs((prev) => {
      const tab: TabItem = { id: nextTabId(), kind: "chat", title: "新会话" };
      setActiveTabId(tab.id);
      return [...prev, tab];
    });
  }, [closeStream]);

  const selectSession = useCallback(
    async (sid: string) => {
      closeStream();
      const info = sessions.find((s) => s.session_id === sid);
      openSessionTab(sid, info?.title || sid);
      if (!chatStatesRef.current[sid]) {
        const snap = await api.getSession(sid);
        patchChat(sid, { ...EMPTY_CHAT, messages: snap?.messages ?? [] });
        try {
          const ctx = await api.contextStats(sid);
          if (ctx.session && Object.keys(ctx.session).length > 0) {
            patchChat(sid, { contextStats: { model: "", context_window: 0, session: ctx.session } });
          }
        } catch {
          /* ignore */
        }
      }
    },
    [closeStream, openSessionTab, patchChat, sessions]
  );

  const deleteSession = useCallback(
    async (id: string) => {
      try {
        await api.deleteSession(id);
        setTabs((prev) => {
          const next = prev.filter((t) => t.sessionId !== id);
          if (next.length === 0) {
            newChatTab();
            return prev;
          }
          if (activeTabId === id || prev.find((t) => t.id === activeTabId)?.sessionId === id) {
            const nb = next[next.length - 1];
            setActiveTabId(nb.id);
          }
          return next;
        });
        await refreshSessions();
      } catch {
        /* ignore */
      }
    },
    [activeTabId, refreshSessions, newChatTab]
  );

  const openProject = useCallback(async () => {
    if (window.liteCode) {
      try {
        const result = await window.liteCode.openProject();
        if (!result.ok && result.error !== "cancelled") {
          patchActiveChat({ error: result.error ?? "无法切换项目" });
        }
        if (result.ok) {
          // Electron 模式刷新页面会丢状态：reload 后启动逻辑直接新建会话
          window.location.reload();
        }
      } catch (e) {
        patchActiveChat({ error: (e as Error).message });
      }
      return;
    }
    setShowPicker(true);
  }, [patchActiveChat]);

  const selectProject = useCallback(
    async (path: string) => {
      setShowPicker(false);
      try {
        const res = await api.setWorkspace(path);
        if (res.ok) {
          setStatus((prev) => (prev ? { ...prev, workspace: res.workspace } : prev));
          setSidebarTab("files");
          setSuccess(`已切换到项目: ${res.workspace}`);
          pushLog(`📂 已切换到项目: ${res.workspace}`);
          setTimeout(() => setSuccess(null), 4000);
          closeStream();
          setChatStates({});
          chatStatesRef.current = {};

          // 切换项目后只刷新历史会话列表，会话由用户按需新建（首条消息才落盘）
          newChatTab();
          await refreshSessions(res.workspace);
        }
      } catch (e) {
        setErrorPublic((e as Error).message);
      }
    },
    [pushLog, refreshSessions, closeStream, newChatTab, patchActiveChat]
  );

  // 首次启动：打开一个占位会话 tab
  useEffect(() => {
    if (loading || tabsRef.current.length > 0) return;
    newChatTab();
  }, [loading, newChatTab]);

  // 会话刷新后同步 chat Tab 标题（用会话名而非 session ID）
  useEffect(() => {
    if (sessions.length === 0 || tabsRef.current.length === 0) return;
    setTabs((prev) => {
      let changed = false;
      const next = prev.map((t) => {
        if (t.kind !== "chat" || !t.sessionId) return t;
        const info = sessions.find((s) => s.session_id === t.sessionId);
        if (!info || !info.title || info.title === t.title) return t;
        changed = true;
        return { ...t, title: info.title };
      });
      return changed ? next : prev;
    });
  }, [sessions]);

  // ------------------------------------------------------------ 流式节流

  const scheduleStreamFlush = useCallback(() => {
    if (flushTimerRef.current !== null) return;
    flushTimerRef.current = window.setTimeout(() => {
      flushTimerRef.current = null;
      const cur = streamingRef.current ?? { content: "", cards: [] };
      const sid = tabsRef.current.find((t) => t.id === activeTabId)?.sessionId;
      if (sid) patchChat(sid, { streaming: { ...cur } });
    }, 80);
  }, [activeTabId, patchChat]);

  const cancelStreamFlush = useCallback(() => {
    if (flushTimerRef.current !== null) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
  }, []);

  // ------------------------------------------------------------ SSE 事件处理

  const handleSSEEvent = useCallback(
    (ev: import("./types").SSEEvent) => {
      lastEventTimeRef.current = Date.now();
      const sid = activeTabId ? tabsRef.current.find((t) => t.id === activeTabId)?.sessionId : null;
      if (!sid) return;
      const st = () => patchChat(sid!, { stalled: false });
      st();
      const log = (msg: string) => pushLog(msg);

      switch (ev.type) {
        case "llm:stream": {
          // SSE chunk 的到达速度高于 React state 提交速度。必须从 ref 读取
          // 已累积内容，否则连续 chunk 会基于旧 state 互相覆盖并缺字。
          const cur = streamingRef.current ?? getChat(sid).streaming ?? { content: "", cards: [] };
          streamingRef.current = { ...cur, content: cur.content + ev.data.chunk };
          scheduleStreamFlush();
          break;
        }
        case "llm:turn_start": {
          log(`⟳ 第 ${ev.data.turn} 轮`);
          const cur = streamingRef.current ?? getChat(sid).streaming ?? { content: "", cards: [] };
          streamingRef.current = { ...cur, turn: ev.data.turn };
          patchChat(sid, { streaming: { ...streamingRef.current } });
          break;
        }
        case "tool:before_execute": {
          log(`⚡ ${ev.data.toolName}`);
          const card: ToolCardInfo = {
            id: `t${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            name: ev.data.toolName,
            args: ev.data.args,
            status: "running",
          };
          const cur = streamingRef.current ?? getChat(sid).streaming ?? { content: "", cards: [] };
          streamingRef.current = { ...cur, cards: [...cur.cards, card] };
          patchChat(sid, { streaming: { ...streamingRef.current } });
          break;
        }
        case "tool:after_execute": {
          log(`✔ ${ev.data.toolName} (${ev.data.durationMs}ms)`);
          if (TREE_TOUCH_TOOLS.has(ev.data.toolName)) setTreeRevision((v) => v + 1);
          const cur = streamingRef.current ?? getChat(sid).streaming;
          if (!cur) break;
          const cards = cur.cards.map((c) =>
            c.name === ev.data.toolName && c.status === "running"
              ? {
                  ...c,
                  status: (ev.data.status === "cancelled" ? "cancelled" : "done") as ToolCardInfo["status"],
                  durationMs: ev.data.durationMs,
                  ...(ev.data.result !== undefined ? { result: ev.data.result } : {}),
                }
              : c
          );
          streamingRef.current = { ...cur, cards };
          patchChat(sid, { streaming: { ...streamingRef.current } });
          break;
        }
        case "approval:request": {
          patchChat(sid, { pendingApproval: { id: ev.data.id, action: ev.data.action, reason: ev.data.reason } });
          break;
        }
        case "approval:resolved": {
          patchChat(sid, { pendingApproval: null });
          break;
        }
        case "stats:update": {
          patchChat(sid, { stats: ev.data });
          break;
        }
        case "context:stats": {
          patchChat(sid, { contextStats: ev.data });
          break;
        }
        case "task:done": {
          setTreeRevision((v) => v + 1);
          const cur = streamingRef.current;
          const finalMsg: Msg = { role: "assistant", content: cur?.content || ev.data.content };
          patchChat(sid, {
            messages: [...getChat(sid).messages, finalMsg],
            stats: ev.data.stats,
            streaming: null,
            running: false,
            turn: 0,
          });
          cancelStreamFlush();
          streamingRef.current = null;
          closeStream();
          taskIdRef.current = null;
          pushLog("■ 任务结束");
          void (async () => {
            const snap = await api.getSession(sid);
            if (snap) patchChat(sid, { messages: snap.messages ?? [] });
          })();
          void refreshSessions();
          break;
        }
        case "task:error": {
          pushLog(`✗ 任务错误: ${ev.data.message}`);
          patchChat(sid, { error: ev.data.message, running: false });
          cancelStreamFlush();
          closeStream();
          taskIdRef.current = null;
          setTreeRevision((v) => v + 1);
          void refreshSessions();
          break;
        }
        case "subagent:completed": {
          setTreeRevision((v) => v + 1);
          log(`◈ 子 Agent ${String(ev.data.role ?? "general")} 已完成`);
          break;
        }
        case "subagent:started": {
          log(`◈ 启动子 Agent ${ev.data.role}`);
          break;
        }
        default:
          break;
      }
    },
    [activeTabId, getChat, patchChat, pushLog, scheduleStreamFlush, cancelStreamFlush, closeStream, refreshSessions]
  );

  // ------------------------------------------------------------ 发送

  const send = useCallback(
    async (prompt: string) => {
      taskIdRef.current = null;
      stopRequestedRef.current = false;
      let sid = activeTabId ? tabsRef.current.find((t) => t.id === activeTabId)?.sessionId : null;
      let createdSession = false;
      if (!sid) {
        // 当前 tab 尚无 session（newChatTab 的占位），首次发送时创建并绑定到该 tab
        const { session_id } = await api.createSession();
        sid = session_id;
        createdSession = true;
        patchChat(session_id, { ...EMPTY_CHAT, messages: [] });
        setTabs((prev) => prev.map((t) =>
          t.id === activeTabId ? { ...t, sessionId: session_id } : t
        ));
      }
      const base = getChat(sid);
      patchChat(sid, {
        messages: [...(base.messages ?? []), { role: "user", content: prompt }],
        error: null,
        running: true,
      });
      cancelStreamFlush();
      streamingRef.current = { content: "", cards: [] };
      patchChat(sid, { streaming: { content: "", cards: [] } });

      const connect = (taskId: string) => {
        const es = new EventSource(`/api/tasks/${taskId}/events`);
        esRef.current = es;
        es.onopen = () => {
          lastEventTimeRef.current = Date.now();
          pushLog(`🔗 已连接任务 ${taskId}`);
        };
        es.onmessage = (e) => {
          if (e.data === "[DONE]") {
            es.close();
            esRef.current = null;
            return;
          }
          try {
            handleSSEEvent(JSON.parse(e.data));
          } catch {
            /* ignore */
          }
        };
        es.onerror = () => {
          pushLog("⚠ SSE 连接中断，等待重连…");
        };
      };

      try {
        pushLog("➤ 提交任务…");
        const { task_id } = await api.chat(sid, prompt, currentAgent);
        taskIdRef.current = task_id;
        if (stopRequestedRef.current) {
          stopRequestedRef.current = false;
          pushLog("■ 任务已提交，立即请求停止…");
          await api.stopTask(task_id).catch(() => {});
        }
        connect(task_id);
      } catch (e) {
        // 创建 session 后提交失败时不留下空历史记录
        if (createdSession && sid) {
          await api.deleteSession(sid).catch(() => {});
          setTabs((prev) => prev.map((tab) =>
            tab.sessionId === sid ? { ...tab, sessionId: undefined, title: "新会话" } : tab
          ));
          void refreshSessions();
        }
        patchChat(sid, { error: (e as Error).message, running: false, streaming: null });
        pushLog(`✗ 提交失败: ${(e as Error).message}`);
      }
    },
    [activeTabId, getChat, patchChat, refreshSessions, cancelStreamFlush, handleSSEEvent, pushLog, currentAgent]
  );

  const stop = useCallback(async () => {
    const tid = taskIdRef.current;
    stopRequestedRef.current = true;
    pushLog("■ 请求停止任务…");
    if (!tid) {
      patchActiveChat({ running: false, streaming: null });
      pushLog("■ 任务尚未提交，已取消发送");
      return;
    }
    try {
      await api.stopTask(tid);
      patchActiveChat({ running: false, streaming: null, pendingApproval: null });
      pushLog("■ 停止请求已发送");
    } catch (e) {
      pushLog(`✗ 停止失败: ${(e as Error).message}`);
    }
  }, [patchActiveChat, pushLog]);

  const approve = useCallback(
    async (approved: boolean) => {
      const pa = currentChat.pendingApproval;
      if (!pa) return;
      patchActiveChat({ pendingApproval: null });
      try {
        await api.approve(pa.id, approved);
      } catch {
        /* ignore */
      }
    },
    [currentChat.pendingApproval, patchActiveChat]
  );

  const activeSessionTitle = useMemo(() => {
    if (!activeSessionId) return "";
    return sessions.find((s) => s.session_id === activeSessionId)?.title ?? "新会话";
  }, [activeSessionId, sessions]);

  const sidebarStats = currentChat.stats;

  if (loading) {
    return (
      <div className="app">
        <div className="drag-region" />
        <div className="loading-screen">
          <div className="loading-spinner" />
          <p>正在连接后端服务…</p>
        </div>
      </div>
    );
  }

  if (!status && currentChat.error) {
    return (
      <div className="app">
        <div className="drag-region" />
        <div className="crash-screen">
          <div className="crash-icon">⚠️</div>
          <h2>无法连接后端服务</h2>
          <p className="crash-message">{currentChat.error}</p>
          <div className="crash-actions">
            <button onClick={() => { patchActiveChat({ error: null }); setLoading(true); void refreshAll(); }}>
              🔄 重试
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <div className="drag-region" />
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        workspace={status?.workspace ?? "…"}
        stats={sidebarStats}
        tab={sidebarTab}
        treeRevision={treeRevision}
        onTabChange={setSidebarTab}
        onSelectSession={(id) => void selectSession(id)}
        onNewSession={newChatTab}
        onDeleteSession={(id) => void deleteSession(id)}
        onOpenProject={() => void openProject()}
        onOpenSettings={() => setShowSettings(true)}
        onFileOpen={(p) => void openFileTab(p)}
      />
      <main className="main">
        <TabBar
          tabs={tabs}
          activeTabId={activeTabId}
          onSelect={(id) => { closeStream(); setActiveTabId(id); }}
          onClose={closeTab}
        />
        {activeTab?.kind === "file" ? (
          <FileViewer tab={activeTab} />
        ) : (
          <>
            <ChatView
              sessionId={activeSessionId ?? "（未选择）"}
              sessionTitle={activeSessionTitle}
              messages={currentChat.messages}
              streaming={currentChat.streaming}
              running={currentChat.running}
              turn={currentChat.turn}
              pendingApproval={currentChat.pendingApproval}
              onSend={(p) => void send(p)}
              onStop={stop}
              onApprove={(a) => void approve(a)}
            />
            <Composer
              disabled={currentChat.running}
              running={currentChat.running}
              agents={agents}
              currentAgent={currentAgent}
              onSelectAgent={setCurrentAgent}
              onSend={(p) => void send(p)}
              onStop={stop}
            />
            <button className="debug-toggle" onClick={() => setShowDebug(!showDebug)} title="调试日志">
              {showDebug ? "隐藏日志" : "日志"}
            </button>
          </>
        )}
        {currentChat.stalled && currentChat.running && (
          <div className="error-banner stalled">
            <span>⚠ 任务长时间无响应（可能 LLM 超时或网络问题）</span>
            <button onClick={stop}>■ 停止任务</button>
          </div>
        )}
        {currentChat.error && (
          <div className="error-banner">
            <span>⚠ {currentChat.error}</span>
            <button onClick={() => patchActiveChat({ error: null })}>✕</button>
          </div>
        )}
        {success && (
          <div className="success-banner">
            <span>✅ {success}</span>
            <button onClick={() => setSuccess(null)}>✕</button>
          </div>
        )}
        {showDebug && (
          <div className="debug-panel">
            <div className="debug-title">调试日志</div>
            <div className="debug-body">
              {debugLogs.length === 0 ? (
                <div className="debug-empty">暂无日志</div>
              ) : (
                debugLogs.map((l, i) => <div key={i} className="debug-line">{l}</div>)
              )}
            </div>
          </div>
        )}
      </main>
      {activeTab?.kind === "chat" && <ToolPanel contextStats={currentChat.contextStats} />}
      {showSettings && (
        <SettingsModal
          onClose={() => setShowSettings(false)}
          onSaved={() => { void refreshAll(); }}
        />
      )}
      {showPicker && (
        <ProjectPicker
          initialPath={status?.workspace ?? ""}
          onClose={() => setShowPicker(false)}
          onSelect={(p) => void selectProject(p)}
        />
      )}
    </div>
  );
}

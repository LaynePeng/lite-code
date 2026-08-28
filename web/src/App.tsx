import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import ChatView from "./components/ChatView";
import Composer from "./components/Composer";
import SettingsModal from "./components/SettingsModal";
import Sidebar from "./components/Sidebar";
import type { AppConfig, Msg, ServerStatus, SessionInfo, Stats, ToolCardInfo } from "./types";

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

export default function App() {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [running, setRunning] = useState(false);
  const [turn, setTurn] = useState(0);
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);

  const esRef = useRef<EventSource | null>(null);
  const taskIdRef = useRef<string | null>(null);
  const streamingRef = useRef<StreamingState | null>(null);
  const messagesRef = useRef<Msg[]>([]);
  const activeSessionRef = useRef<string | null>(null);

  useEffect(() => {
    activeSessionRef.current = activeSessionId;
  }, [activeSessionId]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);
  useEffect(() => {
    streamingRef.current = streaming;
  }, [streaming]);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.sessions());
    } catch {
      /* ignore */
    }
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      const [st, cfg] = await Promise.all([api.status(), api.config()]);
      setStatus(st);
      setConfig(cfg);
    } catch (e) {
      setError((e as Error).message);
    }
    await refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const selectSession = useCallback(
    async (id: string) => {
      closeStream();
      setActiveSessionId(id);
      setMessages([]);
      setStreaming(null);
      setStats(null);
      try {
        const snap = await api.getSession(id);
        setMessages(snap.messages ?? []);
      } catch {
        setMessages([]);
      }
    },
    []
  );

  const newSession = useCallback(async () => {
    try {
      const { session_id } = await api.createSession();
      await selectSession(session_id);
      await refreshSessions();
    } catch {
      /* ignore */
    }
  }, [refreshSessions, selectSession]);

  const deleteSession = useCallback(
    async (id: string) => {
      try {
        await api.deleteSession(id);
        if (activeSessionRef.current === id) {
          setActiveSessionId(null);
          setMessages([]);
          setStreaming(null);
        }
        await refreshSessions();
      } catch {
        /* ignore */
      }
    },
    [refreshSessions]
  );

  const closeStream = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    taskIdRef.current = null;
  }, []);

  // ------------------------------------------------------------ SSE 事件处理

  const handleSSEEvent = useCallback(
    (ev: import("./types").SSEEvent) => {
      switch (ev.type) {
        case "llm:stream": {
          const cur = streamingRef.current ?? { content: "", cards: [] };
          streamingRef.current = { ...cur, content: cur.content + ev.data.chunk };
          setStreaming({ ...streamingRef.current });
          break;
        }
        case "llm:turn_start": {
          setTurn(ev.data.turn);
          const cur = streamingRef.current ?? { content: "", cards: [] };
          streamingRef.current = { ...cur, turn: ev.data.turn };
          setStreaming({ ...streamingRef.current });
          break;
        }
        case "tool:before_execute": {
          const card: ToolCardInfo = {
            id: `t${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            name: ev.data.toolName,
            args: ev.data.args,
            status: "running",
          };
          const cur = streamingRef.current ?? { content: "", cards: [] };
          streamingRef.current = { ...cur, cards: [...cur.cards, card] };
          setStreaming({ ...streamingRef.current });
          break;
        }
        case "tool:after_execute": {
          const cur = streamingRef.current;
          if (!cur) break;
          const cards = cur.cards.map((c) =>
            c.name === ev.data.toolName && c.status === "running"
              ? { ...c, status: (ev.data.status === "cancelled" ? "cancelled" : "done") as ToolCardInfo["status"], durationMs: ev.data.durationMs }
              : c
          );
          streamingRef.current = { ...cur, cards };
          setStreaming({ ...streamingRef.current });
          break;
        }
        case "approval:request": {
          setPendingApproval({ id: ev.data.id, action: ev.data.action, reason: ev.data.reason });
          break;
        }
        case "approval:resolved": {
          setPendingApproval((p) => (p && p.id === ev.data.id ? null : p));
          break;
        }
        case "stats:update": {
          setStats(ev.data);
          break;
        }
        case "task:done": {
          setStats(ev.data.stats);
          const cur = streamingRef.current;
          const finalMsg: Msg = {
            role: "assistant",
            content: cur?.content || ev.data.content,
          };
          setMessages((prev) => [...prev, finalMsg]);
          streamingRef.current = null;
          setStreaming(null);
          setRunning(false);
          setTurn(0);
          closeStream();
          void (async () => {
            const snap = await api.getSession(activeSessionRef.current!);
            if (snap) setMessages(snap.messages ?? []);
          })();
          void refreshSessions();
          break;
        }
        case "task:error": {
          setError(ev.data.message);
          setRunning(false);
          closeStream();
          void refreshSessions();
          break;
        }
        default:
          break;
      }
    },
    [closeStream, refreshSessions]
  );

  // ------------------------------------------------------------ 发送

  const send = useCallback(
    async (prompt: string) => {
      if (!activeSessionRef.current) {
        const { session_id } = await api.createSession();
        setActiveSessionId(session_id);
        activeSessionRef.current = session_id;
        await refreshSessions();
      }
      const sid = activeSessionRef.current;
      setError(null);
      setMessages((prev) => [...prev, { role: "user", content: prompt }]);
      streamingRef.current = { content: "", cards: [] };
      setStreaming({ content: "", cards: [] });
      setRunning(true);

      try {
        const { task_id } = await api.chat(sid!, prompt);
        taskIdRef.current = task_id;
        const es = new EventSource(`/api/tasks/${task_id}/events`);
        esRef.current = es;
        es.onmessage = (e) => {
          if (e.data === "[DONE]") {
            es.close();
            return;
          }
          try {
            handleSSEEvent(JSON.parse(e.data));
          } catch {
            /* ignore */
          }
        };
        es.onerror = () => {
          es.close();
          setRunning(false);
        };
      } catch (e) {
        setError((e as Error).message);
        setRunning(false);
        setStreaming(null);
      }
    },
    [handleSSEEvent, refreshSessions]
  );

  const stop = useCallback(() => {
    if (taskIdRef.current) {
      void api.stopTask(taskIdRef.current).catch(() => {});
    }
  }, []);

  const approve = useCallback(
    async (approved: boolean) => {
      if (!pendingApproval) return;
      const id = pendingApproval.id;
      setPendingApproval(null);
      try {
        await api.approve(id, approved);
      } catch {
        /* ignore */
      }
    },
    [pendingApproval]
  );

  const openProject = useCallback(async () => {
    if (!window.liteCode) return;
    setError(null);
    try {
      const result = await window.liteCode.openProject();
      if (!result.ok) {
        if (result.error !== "cancelled") setError(result.error ?? "无法切换项目");
        return;
      }
      // 主进程已重启后端并重载窗口，这里兜底强制刷新
      window.location.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  return (
    <div className="app">
      <div className="drag-region" />
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        workspace={status?.workspace ?? "…"}
        stats={stats}
        onSelectSession={(id) => void selectSession(id)}
        onNewSession={() => void newSession()}
        onDeleteSession={(id) => void deleteSession(id)}
        onOpenProject={() => void openProject()}
        onOpenSettings={() => setShowSettings(true)}
      />
      <main className="main">
        <ChatView
          sessionId={activeSessionId ?? "（未选择）"}
          messages={messages}
          streaming={streaming}
          running={running}
          turn={turn}
          pendingApproval={pendingApproval}
          onSend={(p) => void send(p)}
          onStop={stop}
          onApprove={(a) => void approve(a)}
        />
        <Composer disabled={running} onSend={(p) => void send(p)} />
        {error && (
          <div className="error-banner">
            <span>⚠ {error}</span>
            <button onClick={() => setError(null)}>✕</button>
          </div>
        )}
      </main>
      {showSettings && (
        <SettingsModal
          onClose={() => setShowSettings(false)}
          onSaved={() => { setShowSettings(false); void refreshAll(); }}
        />
      )}
    </div>
  );
}
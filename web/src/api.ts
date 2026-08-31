import type { AgentInfo, AppConfig, FileDiffResponse, FileReadResponse, LLMConfig, LLMProviderMeta, ServerStatus, SessionInfo, ToolDef, TreeResponse } from "./types";

const TIMEOUT = 15000;

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  try {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      ...init,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail ?? JSON.stringify(body);
      } catch {}
      throw new Error(`${res.status} ${detail}`);
    }
    return res.json() as Promise<T>;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  status: () => req<ServerStatus>("/api/status"),
  config: () => req<AppConfig>("/api/config"),
  updateConfig: (updates: Partial<AppConfig>) =>
    req<{ ok: boolean }>("/api/config", { method: "POST", body: JSON.stringify({ updates }) }),

  agents: () => req<AgentInfo[]>("/api/agents"),

  sessions: (workspace?: string) => {
    const url = workspace ? `/api/sessions?workspace=${encodeURIComponent(workspace)}` : "/api/sessions";
    return req<SessionInfo[]>(url);
  },
  createSession: () =>
    req<{ session_id: string }>("/api/sessions", { method: "POST", body: JSON.stringify({}) }),
  getSession: (id: string) => req<{ messages: import("./types").Msg[] }>(`/api/sessions/${id}`),
  deleteSession: (id: string) =>
    req<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),

  tools: () => req<ToolDef[]>("/api/tools"),
  workspaceTree: (path?: string) =>
    req<TreeResponse>(`/api/workspace/tree-json${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  fsList: (path?: string) =>
    req<{ path: string; parent: string | null; home: string; is_workspace: boolean; dirs: string[]; files: string[]; truncated: boolean }>(
      `/api/fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`
    ),
  readFile: (path: string) =>
    req<FileReadResponse>(`/api/fs/read?path=${encodeURIComponent(path)}`),
  fileDiff: (path: string) =>
    req<FileDiffResponse>(`/api/workspace/diff?path=${encodeURIComponent(path)}`),
  setWorkspace: (path: string) =>
    req<{ ok: boolean; workspace: string }>("/api/workspace", {
      method: "POST", body: JSON.stringify({ path }),
    }),
  security: () => req<Record<string, unknown>>("/api/security"),

  chat: (sessionId: string, prompt: string, agentId?: string) => {
    const body: Record<string, unknown> = { session_id: sessionId, prompt };
    if (agentId && agentId !== "build") body.agent_id = agentId;
    return req<{ task_id: string }>("/api/chat", {
      method: "POST", body: JSON.stringify(body),
    });
  },
  stopTask: (taskId: string) =>
    req<{ ok: boolean }>(`/api/tasks/${taskId}/stop`, { method: "POST" }),
  approve: (approvalId: string, approved: boolean) =>
    req<{ ok: boolean }>("/api/approve", {
      method: "POST", body: JSON.stringify({ approval_id: approvalId, approved }),
    }),

  llmProviders: () => req<LLMProviderMeta[]>("/api/llm/providers"),
  llmConfig: () => req<LLMConfig>("/api/llm/config"),
  updateLLMConfig: (active: string, providers: Record<string, Partial<import("./types").LLMProviderSettings>>) =>
    req<LLMConfig>("/api/llm/config", { method: "POST", body: JSON.stringify({ active, providers }) }),
  testLLM: (providerId: string, overrides?: Record<string, unknown>) =>
    req<{ ok: boolean; message: string; latency_ms: number }>("/api/llm/test", {
      method: "POST", body: JSON.stringify({ provider_id: providerId, overrides }),
    }),

  contextStats: (sessionId: string) =>
    req<{ session: import("./types").ContextSessionStats }>(
      `/api/context/stats?session_id=${encodeURIComponent(sessionId)}`
    ),
};
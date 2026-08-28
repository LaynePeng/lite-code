import type { AppConfig, LLMConfig, LLMProviderMeta, ServerStatus, SessionInfo, ToolDef } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => req<ServerStatus>("/api/status"),
  config: () => req<AppConfig>("/api/config"),
  updateConfig: (updates: Partial<AppConfig>) =>
    req<{ ok: boolean; api_key_configured?: boolean }>("/api/config", {
      method: "POST",
      body: JSON.stringify({ updates }),
    }),

  sessions: () => req<SessionInfo[]>("/api/sessions"),
  createSession: () =>
    req<{ session_id: string }>("/api/sessions", { method: "POST", body: JSON.stringify({}) }),
  getSession: (id: string) => req<{ messages: import("./types").Msg[] }>(`/api/sessions/${id}`),
  deleteSession: (id: string) =>
    req<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),

  tools: () => req<ToolDef[]>("/api/tools"),
  workspaceTree: (depth = 4) =>
    req<{ workspace: string; tree: string }>(`/api/workspace/tree?depth=${depth}`),
  security: () => req<Record<string, unknown>>("/api/security"),

  chat: (sessionId: string, prompt: string) =>
    req<{ task_id: string }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, prompt }),
    }),
  stopTask: (taskId: string) =>
    req<{ ok: boolean }>(`/api/tasks/${taskId}/stop`, { method: "POST" }),
  approve: (approvalId: string, approved: boolean) =>
    req<{ ok: boolean }>("/api/approve", {
      method: "POST",
      body: JSON.stringify({ approval_id: approvalId, approved }),
    }),

  llmProviders: () => req<LLMProviderMeta[]>("/api/llm/providers"),
  llmConfig: () => req<LLMConfig>("/api/llm/config"),
  updateLLMConfig: (active: string, providers: Record<string, Partial<import("./types").LLMProviderSettings>>) =>
    req<LLMConfig>("/api/llm/config", {
      method: "POST",
      body: JSON.stringify({ active, providers }),
    }),
  testLLM: (providerId: string, overrides?: Record<string, unknown>) =>
    req<{ ok: boolean; message: string; latency_ms: number }>("/api/llm/test", {
      method: "POST",
      body: JSON.stringify({ provider_id: providerId, overrides }),
    }),
};
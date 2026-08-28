// 与后端对齐的类型定义

export interface ToolCall {
  id: string;
  type: string;
  function: { name: string; arguments: string };
}

export interface Msg {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  name?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface SessionInfo {
  session_id: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  title: string;
  metadata: Record<string, unknown>;
}

export interface ToolDef {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface Stats {
  input_tokens: number;
  output_tokens: number;
  tool_calls: number;
  turns: number;
  blocked: number;
  cost_estimate: number;
  status: string;
}

export interface ServerStatus {
  version: string;
  workspace: string;
  model: string;
  base_url: string;
  api_key_configured: boolean;
  active_tasks: number;
  sessions_count: number;
  token_auth: boolean;
}

export interface AppConfig {
  max_steps: number;
  token_budget: number;
  tool_timeout: number;
  auto_approve: boolean;
  pricing: { input_per_mtok: number; output_per_mtok: number };
}

export interface LLMProviderMeta {
  id: string;
  name: string;
  kind: "openai" | "anthropic";
  models: string[];
  default_base_url: string;
  has_key: boolean;
  model: string;
}

export interface LLMProviderSettings {
  api_key: string;
  has_key: boolean;
  base_url: string;
  model: string;
  temperature: number;
}

export interface LLMConfig {
  active: string;
  providers: Record<string, LLMProviderSettings>;
}

export type SSEEvent =
  | { type: "message:added"; data: { message: Msg } }
  | { type: "llm:stream"; data: { chunk: string } }
  | { type: "llm:turn_start"; data: { turn: number } }
  | { type: "tool:before_execute"; data: { toolName: string; args: unknown } }
  | { type: "tool:after_execute"; data: { toolName: string; durationMs: number; status: string } }
  | { type: "approval:request"; data: { id: string; action: string; reason: string } }
  | { type: "approval:resolved"; data: { id: string; approved: boolean } }
  | { type: "task:start"; data: { session_id: string } }
  | { type: "task:done"; data: { content: string; stats: Stats } }
  | { type: "task:error"; data: { message: string } }
  | { type: "stats:update"; data: Stats }
  | { type: "subagent:completed"; data: Record<string, unknown> };

// Electron 注入的原生能力（浏览器模式下不存在）
export interface LiteCodeBridge {
  platform: string;
  version: string;
  openProject: () => Promise<{ ok: boolean; url?: string; workspace?: string; error?: string }>;
}

declare global {
  interface Window {
    liteCode?: LiteCodeBridge;
  }
}

// 显示模型：工具卡片
export interface ToolCardInfo {
  id: string;
  name: string;
  args: unknown;
  status: "running" | "done" | "cancelled" | "error";
  durationMs?: number;
  result?: string;
}
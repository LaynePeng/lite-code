// 与后端对齐的类型定义

export interface AgentInfo {
  id: string;
  mode: "primary" | "subagent";
  description: string;
  model: string | null;
  temperature: number | null;
  tools: string[] | null;
  permissions: Record<string, string>;
  hidden: boolean;
}

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

// 目录树条目（/api/workspace/tree-json）
export interface TreeEntry {
  name: string;
  path: string;
  type: "dir" | "file";
  status?: string | null; // git 状态字母: A/M/D/U/R/C
  has_changes?: boolean; // 目录下是否包含改动
}

export interface TreeResponse {
  workspace: string;
  path: string;
  git: { branch: string | null; has_repo: boolean };
  entries: TreeEntry[];
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
  context_full_turns?: number;
  pricing: { input_per_mtok: number; output_per_mtok: number };
}

export interface ContextTaskStats {
  prompt_tokens: number;
  output_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  cache_hit_rate: number | null;
  compression_count: number;
  compressed_tokens: number;
  usage_ratio: number | null;
  last_prompt_tokens: number;
}

export interface ContextSessionStats {
  prompt_tokens: number;
  output_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  cache_hit_rate: number | null;
  compression_count: number;
  compressed_tokens: number;
}

export interface ContextStats {
  model: string;
  context_window: number;
  task?: ContextTaskStats;
  session: ContextSessionStats;
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
  context_window?: number | null;
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
  | { type: "tool:after_execute"; data: { toolName: string; durationMs: number; status: string; result?: string } }
  | { type: "approval:request"; data: { id: string; action: string; reason: string } }
  | { type: "approval:resolved"; data: { id: string; approved: boolean } }
  | { type: "task:start"; data: { session_id: string } }
  | { type: "task:done"; data: { content: string; stats: Stats } }
  | { type: "task:error"; data: { message: string } }
  | { type: "stats:update"; data: Stats }
  | { type: "context:stats"; data: ContextStats }
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

// 文件读取响应（/api/fs/read）
export interface FileReadResponse {
  path: string;
  content: string;
  language: string;
  lines: number;
  size: number;
  diff: string;
}

// 文件 diff 响应（/api/workspace/diff）
export interface FileDiffResponse {
  path: string;
  diff: string;
  additions: number;
  deletions: number;
}

// Tab 项：对话 或 文件
export interface TabItem {
  id: string;
  kind: "chat" | "file";
  title: string;
  // chat tab
  sessionId?: string;
  // file tab
  filePath?: string;
  fileLanguage?: string;
  fileContent?: string;
  fileDiff?: string;
}

// 单个会话的独立状态
export interface ChatSessionState {
  messages: Msg[];
  streaming: { content: string; cards: ToolCardInfo[]; turn?: number } | null;
  running: boolean;
  turn: number;
  stats: Stats | null;
  contextStats: ContextStats | null;
  error: string | null;
  pendingApproval: { id: string; action: string; reason: string } | null;
  stalled: boolean;
}
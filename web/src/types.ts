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
  queued?: boolean; // 任务运行中提交、待注入的补充指令（仅前端标记）
}

// Agent 维护的任务 TODO 清单（todo_write 工具全量覆盖）
export interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
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
  workspace: string | null;
  model: string;
  base_url: string;
  api_key_configured: boolean;
  active_tasks: number;
  sessions_count: number;
  token_auth: boolean;
  active_provider?: string;
}

export interface SessionModel {
  provider: string;
  model: string;
}

export interface SessionModelResponse {
  override: SessionModel | null;
  effective: SessionModel;
}

export interface AppConfig {
  max_steps: number;
  token_budget: number;
  tool_timeout: number;
  llm_timeout?: number;
  auto_approve: boolean;
  context_full_turns?: number;
  pricing: { input_per_mtok: number; output_per_mtok: number };
  /** 技能权限规则：glob 模式 → allow/deny/ask */
  skill_permissions?: Record<string, "allow" | "deny" | "ask">;
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
  last_prompt_tokens?: number;
  tool_calls?: number;
  blocked?: number;
  cost_estimate?: number;
}

export interface ContextSessionStats {
  prompt_tokens: number;
  output_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  cache_hit_rate: number | null;
  compression_count: number;
  compressed_tokens: number;
  tool_calls?: number;
  blocked?: number;
  cost_estimate?: number;
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
  models: string[];
  name?: string;
  temperature: number;
  context_window?: number | null;
  custom_headers?: Record<string, string>;
}

export interface LLMConfig {
  active: string;
  providers: Record<string, LLMProviderSettings>;
}

export type SSEEvent =
  | { type: "message:added"; data: { message: Msg } }
  | { type: "llm:stream"; data: { chunk: string } }
  | { type: "llm:turn_start"; data: { turn: number } }
  | { type: "llm:retry"; data: { attempt: number; max_retries: number; reason: string; wait: number } }
  | { type: "tool:before_execute"; data: { toolName: string; args: unknown; callId?: string } }
  | { type: "tool:after_execute"; data: { toolName: string; durationMs: number; status: string; result?: string; callId?: string } }
  | { type: "approval:request"; data: { id: string; action: string; reason: string } }
  | { type: "approval:resolved"; data: { id: string; approved: boolean } }
  | { type: "task:start"; data: { session_id: string } }
  | { type: "task:done"; data: { content: string; stats: Stats } }
  | { type: "task:error"; data: { message: string } }
  | { type: "stats:update"; data: Stats }
  | { type: "context:stats"; data: ContextStats }
  | { type: "subagent:started"; data: { task: string; role: string; subagentId?: string; callId?: string | null } }
  | { type: "subagent:progress"; data: SubAgentProgressEvent }
  | { type: "subagent:completed"; data: SubAgentCompletedData }
  | { type: "skill:loaded"; data: { names: string[] } }
  | { type: "chat:queued"; data: { text: string; count: number } }
  | { type: "todo:updated"; data: { todos: TodoItem[] } };

// 子 Agent 实时进度事件（命名空间转发自隔离 kernel）
export interface SubAgentProgressEvent {
  subagentId: string;
  role: string;
  callId: string | null;
  kind: "llm:turn_start" | "tool:before_execute" | "tool:after_execute";
  turn?: number;
  tool?: string;
  brief?: string;
  status?: string;
  durationMs?: number;
}

export interface SubAgentCompletedData {
  task: string;
  role: string;
  subagentId?: string;
  callId?: string | null;
  tokens_used?: number;
  turns?: number;
  summary?: string;
}

// 子 Agent 活动卡片（嵌在 spawn_sub_agent 工具卡内渲染）
export interface SubAgentStep {
  tool: string;
  brief?: string;
  status: "running" | "done" | "error" | "cancelled";
  durationMs?: number;
}

export interface SubAgentProgress {
  subagentId: string;
  role: string;
  task: string;
  turn: number;
  steps: SubAgentStep[];
  status: "running" | "done" | "error";
  summary?: string;
  tokens?: number;
}

// Electron 注入的原生能力（浏览器模式下不存在）
export interface LiteCodeBridge {
  platform: string;
  version: string;
  openProject: () => Promise<{ ok: boolean; url?: string; workspace?: string; error?: string }>;
  openProjectNewWindow: () => Promise<{ ok: boolean; url?: string; workspace?: string; error?: string }>;
  terminalStart: (cols?: number, rows?: number) => Promise<{ ok: boolean; error?: string }>;
  terminalInput: (data: string) => void;
  terminalResize: (cols: number, rows: number) => void;
  terminalStop: () => void;
  onTerminalData: (listener: (data: string) => void) => () => void;
  onTerminalExit: (listener: (code: number) => void) => () => void;
  onShowAbout: (listener: () => void) => () => void;
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
  callId?: string;
  subagent?: SubAgentProgress;
}

// 按 SSE / 会话消息原始顺序排列的工作时间线
export type WorkItem =
  | { type: "text"; id: string; content: string }
  | { type: "tool"; id: string; card: ToolCardInfo }
  | { type: "activity"; id: string; tools: ToolCardInfo[] };

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
  modelOverride?: SessionModel | null;
  // file tab
  filePath?: string;
  fileLanguage?: string;
  fileContent?: string;
  fileDiff?: string;
}

// 单个会话的独立状态
export interface ChatSessionState {
  messages: Msg[];
  streaming: { items: WorkItem[]; turn?: number } | null;
  running: boolean;
  turn: number;
  stats: Stats | null;
  contextStats: ContextStats | null;
  error: string | null;
  // 审批队列：并行工具可同时挂起多个审批请求
  pendingApprovals: { id: string; action: string; reason: string }[];
  // 任务完成后归档的子 Agent 活动卡（会话级内存态，刷新即失）
  subAgentRecords: SubAgentProgress[];
  stalled: boolean;
  modelOverride?: SessionModel | null;
  effectiveModel?: SessionModel;
  skillLoaded?: string[];
  // 任务 TODO 清单（todo_write 工具推送，任务结束后保留展示）
  todos: TodoItem[];
}

// ---------------------------------------------------------------- Skills 管理与命令

export interface SkillInfo {
  name: string;
  description: string;
  dirName: string;
  path: string;
  scope: "workspace" | "user";
  writable: boolean;
  triggers: string;
  /** 权限动作：allow（默认）/ deny（对 Agent 隐藏）/ ask（使用前需确认） */
  permission?: "allow" | "deny" | "ask";
}

export interface CommandInfo {
  name: string;
  description: string;
  argsHint: string;
  kind: "builtin" | "skill";
}

// ---------------------------------------------------------------- MCP 配置

export interface MCPServerConfig {
  command: string;
  args?: string[];
  env?: Record<string, string>;
  enabled?: boolean;
}

export interface MCPServerStatus {
  name: string;
  command: string;
  args: string[];
  enabled: boolean;
  connected: boolean;
  error?: string | null;
  tools: string[];
}

export interface MCPStatus {
  servers: MCPServerStatus[];
}

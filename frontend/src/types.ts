// 前后端共享的事件/数据契约。
// 对应 axiom_core/api/events.py + sessions.py 的输出结构。

export interface ModelConfig {
  base_url: string;
  api_key: string;
  model: string;
}

export interface LLMProvider {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  model: string;
  context_window: number;
  max_tokens: number;
  enabled: boolean;
}

export interface MCPServer {
  id: string;
  name: string;
  url: string;
  key: string; // 映射到 headers.Authorization = "Bearer <key>"
  enabled: boolean;
}

export interface VerificationConfig {
  enabled: boolean;
  reviewer_model?: string;
  reviewer_max_iterations?: number;
  reviewer_axiom_budget?: number;
  shadow_reviewer?: boolean;
  bookmarks_enabled?: boolean;
  max_consecutive_bounces?: number;
  min_checkpoint_interval_ms?: number;
}

export interface TraceConfig {
  enabled: boolean;
  log_dir?: string;
  retention_days?: number;
  log_llm_payload?: boolean;
  log_tool_args?: boolean;
  log_tool_result_summary?: boolean;
}

export interface RegistryConfig {
  enabled: boolean;
  url: string;
  api_key: string;
}

export interface A2AConfig {
  default_timeout: number; // 秒
}

export interface AppSettings {
  version: number;
  llm_providers: LLMProvider[];
  mcp_servers: MCPServer[];
  api_keys: Record<string, string>;
  workspace: string;
  plan_mode: boolean;
  default_model_tier: string;
  disabled_skills: string[];
  load_claude_skills: boolean;
  load_project_skills: boolean;
  skill_extra_dirs: string[];
  verification: VerificationConfig;
  trace: TraceConfig;
  default_template: string;
  registry: RegistryConfig;
  a2a: A2AConfig;
}

export interface SkillInfo {
  name: string;
  description: string;
  source: "anthropic" | "global" | "claude" | "project" | "custom";
  enabled: boolean;
}

export interface McpToolInfo {
  name: string;
  description: string;
}

export interface McpServerStatus {
  name: string;
  url: string;
  enabled: boolean;
  connected: boolean;
  tools: McpToolInfo[];
  error?: string;
}

export interface MemoryInfo {
  id: string;
  // 老的作用域字段 (profile/project/frame), 向后兼容
  entity: string;
  // Layer A: 作用域 (与 entity 语义一致) + 语义类型 + 结构化字段
  scope?: string;
  entity_type?: string;  // claim / evidence / citation / tool_use / note
  meta?: Record<string, unknown> | null;
  session_id?: string | null;
  confidence?: number;
  body: string;
  evidence: string;
  origin: string;
  frame_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SessionInfo {
  id: string;
  title: string | null;
  status: string;
  workspace: string;
  model: string | null;
  plan_mode: boolean;
  // Layer A.5: 所属 project id (老 session 是 'proj_default')
  project_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  live: boolean;
}

export interface SessionState {
  id: string;
  frame_id: string | null;
  status: string;
  task_summary: string | null;
  plan_mode: boolean;
  plan: PlanSnapshot | null;
  artifacts: Record<string, ArtifactInfo>;
  messages: SessionMessage[];
}

export interface SessionMessage {
  role: string;
  content: unknown;
  harness_notice?: boolean | null;
}

// Layer A.5: Project
export interface ProjectInfo {
  id: string;
  name: string;
  description: string | null;
  last_session_id: string | null;
  session_count: number;
  last_activity_at: string | null;
  created_at: string | null;
  is_default: boolean;
  archived: boolean;
}

// WebSocket 事件 (对应 axiom_core/api/callbacks.py 发出的 dict)
export type WSEvent =
  | { type: "start"; frame_id: string; task_summary: string }
  | { type: "iteration"; n: number }
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_calls"; calls: ToolCall[] }
  | { type: "tool_results"; results: ToolResult[] }
  | { type: "plan_update"; plan: PlanSnapshot }
  | { type: "notice"; event: string; detail: string }
  | {
      type: "complete";
      kind: string;
      final_text: string;
      awaiting: string | null;
      pending_ask: PendingAsk | null;
      error: string | null;
      usage: Record<string, number>;
      iterations: number;
      frame_status: string;
      plan: PlanSnapshot | null;
      artifacts: Record<string, ArtifactInfo>;
    }
  | { type: "error"; message: string };

export interface PendingAsk {
  question: string;
  options: string[];
}

export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface ToolResult {
  tool_use_id: string;
  content: string;
  is_error: boolean;
}

export interface PlanStep {
  id: string;
  description: string;
  status: string;
  note?: string;
}

export interface PlanSnapshot {
  steps: PlanStep[];
  approved: boolean;
  research_question?: string;
  scope?: string;
  desired_outputs?: string[];
  feasibility?: { confidence: string; rationale: string };
}

export interface ArtifactInfo {
  path: string;
  size: number;
  frame_id: string;
}

export interface TemplateInfo {
  id: string;
  name: string;
  description: string;
  documentclass: string;
  columns: number;
}

export interface CompileResult {
  success: boolean;
  pdf_path: string;
  size_kb: number;
  message: string;
  errors: string[];
  log_excerpt: string;
}

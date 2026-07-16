// 前后端共享的事件/数据契约。
// 对应 operon/api/events.py + sessions.py 的输出结构。

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
  reviewer_operon_budget?: number;
  shadow_reviewer?: boolean;
  bookmarks_enabled?: boolean;
  max_consecutive_bounces?: number;
  min_checkpoint_interval_ms?: number;
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
  entity: string;
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
  created_at: string | null;
  updated_at: string | null;
  live: boolean;
}

// WebSocket 事件 (对应 operon/api/callbacks.py 发出的 dict)
export type WSEvent =
  | { type: "start"; frame_id: string; task_summary: string }
  | { type: "iteration"; n: number }
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_calls"; calls: ToolCall[] }
  | { type: "tool_results"; results: ToolResult[] }
  | { type: "notice"; event: string; detail: string }
  | { type: "complete"; kind: string; final_text: string; awaiting: string | null; error: string | null; usage: Record<string, number>; iterations: number; frame_status: string; plan: PlanSnapshot | null; artifacts: Record<string, ArtifactInfo> }
  | { type: "error"; message: string };

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

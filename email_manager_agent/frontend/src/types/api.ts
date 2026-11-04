// Types mirroring the backend HTTP API (see README "API").

export interface ServiceStatus {
  configured: boolean;
  issues: string[];
  reachable: boolean | null;
  detail: string | null;
}

export interface LlmStatus extends ServiceStatus {
  provider: "ollama" | "claude" | string;
  model: string | null;
}

export interface Limits {
  max_agent_steps: number;
  max_prompt_chars: number;
  approval_ttl_seconds: number;
  max_search_results: number;
  max_attachment_mb: number;
  work_days: number[];
  work_start: string;
  work_end: string;
}

export interface PreviewDetail {
  label: string;
  value: string;
}

export interface PendingOperation {
  operation_id: string;
  tool: string;
  title: string;
  summary: string;
  details: PreviewDetail[];
  body: string | null;
  warnings: string[];
  requires_acknowledgement: boolean;
  created_at: string;
  expires_at: string;
}

export interface StatusResponse {
  llm: LlmStatus;
  bridge: ServiceStatus;
  timezone: string;
  limits: Limits;
  session_id: string | null;
  pending_operations: PendingOperation[];
}

export type ToolCallStatus = "ok" | "error" | "invalid" | "pending";

export interface ToolCall {
  name: string;
  status: ToolCallStatus;
  summary: string;
  operation_id: string | null;
}

export type StopReason = "completed" | "max_steps" | "refusal" | "max_tokens" | string;

export interface PromptResponse {
  session_id: string;
  reply: string;
  steps: number;
  stop_reason: StopReason;
  tool_calls: ToolCall[];
  pending_operations: PendingOperation[];
}

export type Decision = "approve" | "reject";

export interface ConfirmRequest {
  session_id: string;
  operation_id: string;
  decision: Decision;
  acknowledge_warnings?: boolean;
}

export interface ConfirmResponse {
  status: "executed" | "rejected" | "failed";
  message: string;
  result: unknown;
  pending_operations: PendingOperation[];
}

export interface ResetResponse {
  session_id: string;
  status: "reset";
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

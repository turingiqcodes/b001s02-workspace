import type { PendingOperation, PromptResponse, StatusResponse } from "../types/api";

export function makeStatus(overrides: Partial<StatusResponse> = {}): StatusResponse {
  return {
    llm: { provider: "ollama", model: "qwen3:8b", configured: true, issues: [], reachable: null, detail: null },
    bridge: { configured: true, issues: [], reachable: null, detail: null },
    timezone: "Asia/Kolkata",
    limits: {
      max_agent_steps: 8,
      max_prompt_chars: 4000,
      approval_ttl_seconds: 600,
      max_search_results: 25,
      max_attachment_mb: 20,
      work_days: [0, 1, 2, 3, 4],
      work_start: "09:00",
      work_end: "18:00",
    },
    session_id: null,
    pending_operations: [],
    ...overrides,
  };
}

export function makeOp(overrides: Partial<PendingOperation> = {}): PendingOperation {
  return {
    operation_id: "op_" + "a".repeat(24),
    tool: "send_email",
    title: "Send email",
    summary: 'To alice@example.com — "Report"',
    details: [{ label: "To", value: "alice@example.com" }],
    body: "Hi Alice",
    warnings: [],
    requires_acknowledgement: false,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 600_000).toISOString(),
    ...overrides,
  };
}

export function makePromptResponse(overrides: Partial<PromptResponse> = {}): PromptResponse {
  return {
    session_id: "s1",
    reply: "Here you go.",
    steps: 2,
    stop_reason: "completed",
    tool_calls: [{ name: "search_emails", status: "ok", summary: "Searched email", operation_id: null }],
    pending_operations: [],
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function errorResponse(code: string, message: string, status: number): Response {
  return jsonResponse({ error: { code, message } }, status);
}

type Route = (url: string, init?: RequestInit) => Response | Promise<Response>;

/** Install a fetch mock that dispatches on "METHOD /path" and records calls. */
export function mockFetch(routes: Record<string, Route | Route[]>) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  const queues: Record<string, Route[]> = {};
  for (const [k, v] of Object.entries(routes)) queues[k] = Array.isArray(v) ? [...v] : [v];
  const fn = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.split("?")[0];
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const q = queues[`${method} ${path}`];
    if (!q || q.length === 0) throw new Error(`Unexpected fetch ${method} ${url}`);
    const handler = q.length > 1 ? q.shift()! : q[0];
    return handler(url, init);
  };
  globalThis.fetch = fn as typeof fetch;
  return calls;
}

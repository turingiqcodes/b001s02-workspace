import type {
  ConfirmRequest,
  ConfirmResponse,
  PromptResponse,
  ResetResponse,
  StatusResponse,
} from "../types/api";

/** Error raised for any failed API call. `code` is the backend error code, or "backend_offline". */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

export const OFFLINE_MESSAGE =
  "Backend offline. Start the backend (python run.py in backend/) and check port 8000 is free.";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("backend_offline", OFFLINE_MESSAGE, 0);
  }

  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!res.ok) {
    const err = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
    if (err && typeof err.code === "string" && typeof err.message === "string") {
      throw new ApiError(err.code, err.message, res.status);
    }
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      if (body === null) throw new ApiError("backend_offline", OFFLINE_MESSAGE, res.status);
    }
    throw new ApiError("http_error", `Request failed (HTTP ${res.status}).`, res.status);
  }
  if (body === null) {
    throw new ApiError("bad_response", "The backend returned an unexpected response.", res.status);
  }
  return body as T;
}

export function getStatus(sessionId: string | null, checkBridge = false): Promise<StatusResponse> {
  const params = new URLSearchParams();
  if (sessionId) params.set("session_id", sessionId);
  if (checkBridge) params.set("check_bridge", "true");
  const qs = params.toString();
  return request<StatusResponse>(`/status${qs ? `?${qs}` : ""}`);
}

export function sendPrompt(sessionId: string | null, prompt: string): Promise<PromptResponse> {
  const body: { prompt: string; session_id?: string } = { prompt };
  if (sessionId) body.session_id = sessionId;
  return request<PromptResponse>("/prompt", { method: "POST", body: JSON.stringify(body) });
}

export function confirmOperation(req: ConfirmRequest): Promise<ConfirmResponse> {
  return request<ConfirmResponse>("/confirm", { method: "POST", body: JSON.stringify(req) });
}

export function resetSession(sessionId: string | null): Promise<ResetResponse> {
  return request<ResetResponse>("/reset", {
    method: "POST",
    body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
  });
}

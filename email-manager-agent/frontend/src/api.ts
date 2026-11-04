import type { TurnResponse } from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Can't reach the backend. Is it running on port 8000?", 0);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `Request failed (HTTP ${res.status}).`;
    throw new ApiError(detail, res.status);
  }
  return body as T;
}

export const api = {
  createSession: () => request<{ session_id: string }>("/api/sessions", { method: "POST" }),
  deleteSession: (id: string) => request<{ deleted: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),
  send: (id: string, text: string) =>
    request<TurnResponse>(`/api/sessions/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  decide: (id: string, decisions: { action_id: string; approve: boolean }[]) =>
    request<TurnResponse>(`/api/sessions/${id}/decisions`, { method: "POST", body: JSON.stringify({ decisions }) }),
  health: () =>
    request<{ status: string; model: string; bridge_mode: string; anthropic_key_configured: boolean }>("/api/health"),
};

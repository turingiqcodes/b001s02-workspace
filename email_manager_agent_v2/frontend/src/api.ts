import type { ConfirmResponse, PromptResponse, Status } from './types';

export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Can't reach the backend. Is it running on port 8000?", 0);
  }
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : `Request failed (HTTP ${resp.status}).`;
    throw new ApiError(detail, resp.status, body?.code);
  }
  return body as T;
}

export const api = {
  status: () => request<Status>('/api/status'),
  prompt: (prompt: string, sessionId: string | null) =>
    request<PromptResponse>('/api/prompt', {
      method: 'POST',
      body: JSON.stringify(sessionId ? { prompt, session_id: sessionId } : { prompt }),
    }),
  confirm: (actionId: string, approved: boolean) =>
    request<ConfirmResponse>('/api/confirm', {
      method: 'POST',
      body: JSON.stringify({ action_id: actionId, approved }),
    }),
  reset: (sessionId: string | null) =>
    request<{ status: string; message: string }>('/api/reset', {
      method: 'POST',
      body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
    }),
};

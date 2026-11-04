import type {
  ConfirmResponse,
  PendingOperation,
  PromptResponse,
  StatusResponse,
  ToolCall,
} from "../types/api";

export type ChatKind = "user" | "assistant" | "notice" | "error";

export interface ChatItem {
  id: string;
  kind: ChatKind;
  text: string;
  toolCalls?: ToolCall[];
  stopReason?: string;
}

export interface AppState {
  sessionId: string | null;
  items: ChatItem[];
  pending: PendingOperation[];
  status: StatusResponse | null;
  statusError: string | null;
  busy: boolean;
  checking: boolean;
  /** Operation ids with a confirm request in flight. */
  opBusy: Record<string, boolean>;
  /** Per-operation error messages (e.g. acknowledgement required). */
  opErrors: Record<string, string>;
}

export type Action =
  | { type: "status_loaded"; status: StatusResponse }
  | { type: "status_failed"; message: string }
  | { type: "check_started" }
  | { type: "check_finished" }
  | { type: "prompt_started"; text: string }
  | { type: "prompt_succeeded"; response: PromptResponse }
  | { type: "prompt_failed"; message: string }
  | { type: "session_lost"; message: string }
  | { type: "confirm_started"; operationId: string }
  | { type: "confirm_succeeded"; operationId: string; response: ConfirmResponse }
  | { type: "confirm_failed"; operationId: string; message: string; remove: boolean }
  | { type: "pending_replaced"; pending: PendingOperation[] }
  | { type: "operation_expired"; operationId: string }
  | { type: "reset"; sessionId: string | null };

export const initialState: AppState = {
  sessionId: null,
  items: [],
  pending: [],
  status: null,
  statusError: null,
  busy: false,
  checking: false,
  opBusy: {},
  opErrors: {},
};

let counter = 0;
export function nextId(): string {
  counter += 1;
  return `item-${counter}`;
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record;
  const copy = { ...record };
  delete copy[key];
  return copy;
}

function stopNote(stopReason: string): string | null {
  switch (stopReason) {
    case "max_steps":
      return "Stopped after the step limit. Try a smaller or more specific request.";
    case "max_tokens":
      return "The answer was cut off because it was too long.";
    case "refusal":
      return "The model declined this request.";
    default:
      return null;
  }
}

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "status_loaded": {
      const s = action.status;
      // Pending operations from status are authoritative only for the current session.
      const pending = s.session_id && s.session_id === state.sessionId ? s.pending_operations : state.pending;
      return { ...state, status: s, statusError: null, pending };
    }
    case "status_failed":
      return { ...state, statusError: action.message };
    case "check_started":
      return { ...state, checking: true };
    case "check_finished":
      return { ...state, checking: false };
    case "prompt_started":
      return {
        ...state,
        busy: true,
        items: [...state.items, { id: nextId(), kind: "user", text: action.text }],
      };
    case "prompt_succeeded": {
      const r = action.response;
      const items: ChatItem[] = [
        ...state.items,
        {
          id: nextId(),
          kind: "assistant",
          text: r.reply || "(No reply.)",
          toolCalls: r.tool_calls,
          stopReason: r.stop_reason,
        },
      ];
      const note = stopNote(r.stop_reason);
      if (note) items.push({ id: nextId(), kind: "notice", text: note });
      return { ...state, busy: false, sessionId: r.session_id, items, pending: r.pending_operations };
    }
    case "prompt_failed":
      return {
        ...state,
        busy: false,
        items: [...state.items, { id: nextId(), kind: "error", text: action.message }],
      };
    case "session_lost":
      return {
        ...state,
        busy: false,
        sessionId: null,
        pending: [],
        opBusy: {},
        opErrors: {},
        items: [...state.items, { id: nextId(), kind: "error", text: action.message }],
      };
    case "confirm_started":
      return {
        ...state,
        opBusy: { ...state.opBusy, [action.operationId]: true },
        opErrors: without(state.opErrors, action.operationId),
      };
    case "confirm_succeeded": {
      const r = action.response;
      const kind: ChatKind = r.status === "failed" ? "error" : "notice";
      return {
        ...state,
        pending: r.pending_operations,
        opBusy: without(state.opBusy, action.operationId),
        opErrors: without(state.opErrors, action.operationId),
        items: [...state.items, { id: nextId(), kind, text: r.message }],
      };
    }
    case "confirm_failed": {
      const opBusy = without(state.opBusy, action.operationId);
      if (action.remove) {
        return {
          ...state,
          opBusy,
          opErrors: without(state.opErrors, action.operationId),
          pending: state.pending.filter((p) => p.operation_id !== action.operationId),
          items: [...state.items, { id: nextId(), kind: "error", text: action.message }],
        };
      }
      return { ...state, opBusy, opErrors: { ...state.opErrors, [action.operationId]: action.message } };
    }
    case "pending_replaced":
      return { ...state, pending: action.pending };
    case "operation_expired":
      return {
        ...state,
        pending: state.pending.filter((p) => p.operation_id !== action.operationId),
        opBusy: without(state.opBusy, action.operationId),
        opErrors: without(state.opErrors, action.operationId),
      };
    case "reset":
      return {
        ...initialState,
        sessionId: action.sessionId,
        status: state.status,
        statusError: state.statusError,
      };
    default:
      return state;
  }
}

export interface PreviewField {
  label: string;
  value: string;
}

export interface PendingAction {
  action_id: string;
  tool: string;
  title: string;
  fields: PreviewField[];
  body: string | null;
  warning: string | null;
}

export interface ExecutedAction {
  action_id: string;
  title: string;
  status: "done" | "rejected" | "failed";
  detail: string | null;
}

export interface Activity {
  tool: string;
  label: string;
  ok: boolean;
}

export interface TurnResponse {
  session_id: string;
  state: "idle" | "awaiting_approval";
  reply: string;
  activity: Activity[];
  executed: ExecutedAction[];
  pending: PendingAction[];
  error: string | null;
}

export type ChatItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string; activity: Activity[]; executed: ExecutedAction[] }
  | { kind: "error"; id: string; text: string };

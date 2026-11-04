export type Status = {
  llm_configured: boolean;
  llm_model: string;
  bridge_configured: boolean;
  gmail_connected: boolean;
  gmail_address: string | null;
  time_zone: string | null;
  status: 'ready' | 'not_configured' | 'bridge_error';
  detail: string | null;
};

export type Preview = {
  title: string;
  summary: string;
  target: Record<string, unknown> | null;
  existing: Record<string, unknown> | null;
  proposed: Record<string, unknown> | null;
  recipients: string[] | null;
  availability: { free: boolean; conflicts: string[] } | null;
  notify_attendees: boolean | null;
  warnings: string[];
  irreversible: boolean;
};

export type ActionStatus = 'needs_confirmation' | 'executed' | 'declined' | 'failed';

export type PendingAction = {
  action_id: string;
  operation: string;
  status: ActionStatus;
  preview: Preview;
  created_at: string;
  message: string | null;
};

export type PromptResponse = {
  session_id: string;
  response: string;
  pending_actions: PendingAction[];
  declined_actions: string[];
};

export type ConfirmResponse = {
  action_id: string;
  status: 'executed' | 'declined' | 'failed';
  message: string;
};

export type FeedItem =
  | { id: string; kind: 'user'; text: string }
  | { id: string; kind: 'assistant'; text: string }
  | { id: string; kind: 'action'; action: PendingAction }
  | { id: string; kind: 'error'; text: string };

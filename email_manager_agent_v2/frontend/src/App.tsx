import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { ApprovalCard } from './components/ApprovalCard';
import { StatusBar } from './components/StatusBar';
import type { FeedItem, PendingAction, Status } from './types';

const SESSION_KEY = 'email-manager-session';
const EXAMPLES = [
  'Summarize my latest 5 emails',
  'Find the latest email from Priya and draft a reply',
  'What meetings do I have tomorrow?',
  'Find a free 30-minute slot this week',
];

function loadSession(): string | null {
  try {
    return sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function saveSession(id: string | null) {
  try {
    if (id) sessionStorage.setItem(SESSION_KEY, id);
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable: session lives in memory only */
  }
}

let counter = 0;
const uid = () => `item_${Date.now()}_${counter++}`;

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(loadSession);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [prompt, setPrompt] = useState('');
  const [thinking, setThinking] = useState(false);
  const [deciding, setDeciding] = useState<string | null>(null);
  const feedEnd = useRef<HTMLDivElement>(null);

  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      setStatus(await api.status());
      setStatusError(null);
    } catch (e) {
      setStatus(null);
      setStatusError(e instanceof Error ? e.message : String(e));
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    feedEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [feed, thinking]);

  const updateAction = (actionId: string, patch: Partial<PendingAction>) =>
    setFeed((items) =>
      items.map((it) => (it.kind === 'action' && it.action.action_id === actionId ? { ...it, action: { ...it.action, ...patch } } : it)),
    );

  const submit = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || thinking) return;
    setPrompt('');
    setFeed((f) => [...f, { id: uid(), kind: 'user', text: trimmed }]);
    setThinking(true);
    try {
      const res = await api.prompt(trimmed, sessionId);
      setSessionId(res.session_id);
      saveSession(res.session_id);
      // FR-37: anything still pending was declined by this new prompt.
      res.declined_actions.forEach((id) =>
        updateAction(id, { status: 'declined', message: 'Cancelled because you sent a new message.' }),
      );
      setFeed((f) => [
        ...f,
        { id: uid(), kind: 'assistant', text: res.response },
        ...res.pending_actions.map((a) => ({ id: uid(), kind: 'action' as const, action: a })),
      ]);
    } catch (e) {
      setFeed((f) => [...f, { id: uid(), kind: 'error', text: e instanceof Error ? e.message : String(e) }]);
      if (e instanceof ApiError && e.status === 0) void refreshStatus();
    } finally {
      setThinking(false);
    }
  };

  const decide = async (actionId: string, approved: boolean) => {
    setDeciding(actionId);
    try {
      const res = await api.confirm(actionId, approved);
      updateAction(actionId, { status: res.status, message: res.message });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (e instanceof ApiError && (e.status === 404 || e.code === 'action_not_pending')) {
        updateAction(actionId, { status: 'declined', message: msg });
      } else {
        setFeed((f) => [...f, { id: uid(), kind: 'error', text: msg }]);
      }
    } finally {
      setDeciding(null);
    }
  };

  const reset = async () => {
    try {
      await api.reset(sessionId);
    } catch (e) {
      setFeed((f) => [...f, { id: uid(), kind: 'error', text: e instanceof Error ? e.message : String(e) }]);
      return;
    }
    setSessionId(null);
    saveSession(null);
    setFeed([]);
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void submit(prompt);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void submit(prompt);
    }
  };

  return (
    <div className="app">
      <StatusBar
        status={status}
        loading={statusLoading}
        error={statusError}
        onRefresh={refreshStatus}
        onReset={reset}
        resetDisabled={thinking || deciding !== null}
      />

      <main className="feed" aria-live="polite">
        {feed.length === 0 && (
          <div className="empty">
            <h1>What would you like to do?</h1>
            <p className="muted">
              Ask about your email, calendar or Drive. Nothing is sent, changed or deleted until you approve it.
            </p>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} type="button" className="chip" onClick={() => void submit(ex)} disabled={thinking}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {feed.map((item) => {
          switch (item.kind) {
            case 'user':
              return (
                <div key={item.id} className="msg msg-user">
                  {item.text}
                </div>
              );
            case 'assistant':
              return (
                <div key={item.id} className="msg msg-assistant">
                  {item.text}
                </div>
              );
            case 'error':
              return (
                <div key={item.id} className="msg msg-error" role="alert">
                  {item.text}
                </div>
              );
            case 'action':
              return <ApprovalCard key={item.id} action={item.action} busy={deciding !== null || thinking} onDecide={decide} />;
          }
        })}

        {thinking && <div className="msg msg-assistant muted">Working on it… Larger requests can take up to a minute on Groq's free plan.</div>}
        <div ref={feedEnd} />
      </main>

      <form className="composer" onSubmit={onSubmit}>
        <label htmlFor="prompt" className="sr-only">
          Your request
        </label>
        <textarea
          id="prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="e.g. Move tomorrow's design review to 4 PM"
          rows={2}
          maxLength={8000}
          disabled={thinking}
        />
        <button type="submit" className="btn btn-primary" disabled={thinking || !prompt.trim()}>
          {thinking ? 'Working…' : 'Send'}
        </button>
      </form>
    </div>
  );
}

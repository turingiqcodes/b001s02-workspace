import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { ApprovalCard } from "./components/ApprovalCard";
import { Message } from "./components/Message";
import type { ChatItem, PendingAction, TurnResponse } from "./types";

const EXAMPLES = [
  "Summarize my unread emails",
  "Am I free tomorrow afternoon?",
  "Draft a reply to the latest email from Priya",
  "Save the invoice attachment to Drive",
];

let counter = 0;
const nextId = () => `i${++counter}`;

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [decisions, setDecisions] = useState<Record<string, boolean>>({});
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string>("");
  const endRef = useRef<HTMLDivElement>(null);

  const addError = (text: string) => setItems((xs) => [...xs, { kind: "error", id: nextId(), text }]);

  const startSession = useCallback(async () => {
    try {
      const { session_id } = await api.createSession();
      setSessionId(session_id);
      return session_id;
    } catch (e) {
      addError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, []);

  useEffect(() => {
    startSession();
    api.health()
      .then((h) => setStatus(`${h.model} · ${h.bridge_mode === "mock" ? "demo data" : "Google account"}`
        + (h.anthropic_key_configured ? "" : " · API key missing")))
      .catch(() => setStatus("backend offline"));
  }, [startSession]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items, pending]);

  const applyTurn = (r: TurnResponse) => {
    setItems((xs) => {
      const next = [...xs];
      if (r.reply || r.activity.length || r.executed.length) {
        next.push({ kind: "assistant", id: nextId(), text: r.reply, activity: r.activity, executed: r.executed });
      }
      if (r.error) next.push({ kind: "error", id: nextId(), text: r.error });
      return next;
    });
    setPending(r.pending);
    setDecisions({});
  };

  const handleFailure = async (e: unknown) => {
    if (e instanceof ApiError && e.status === 404) {
      addError("This conversation expired, so a new one was started. Please send your request again.");
      setPending([]);
      await startSession();
      return;
    }
    addError(e instanceof Error ? e.message : String(e));
  };

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy || pending.length) return;
    const sid = sessionId ?? (await startSession());
    if (!sid) return;
    setItems((xs) => [...xs, { kind: "user", id: nextId(), text: trimmed }]);
    setInput("");
    setBusy(true);
    try {
      applyTurn(await api.send(sid, trimmed));
    } catch (e) {
      await handleFailure(e);
    } finally {
      setBusy(false);
    }
  };

  const decide = async (actionId: string, approve: boolean) => {
    const next = { ...decisions, [actionId]: approve };
    setDecisions(next);
    if (!sessionId || pending.some((p) => next[p.action_id] === undefined)) return;
    setBusy(true);
    try {
      applyTurn(await api.decide(sessionId, pending.map((p) => ({ action_id: p.action_id, approve: next[p.action_id] }))));
    } catch (e) {
      setDecisions({});
      await handleFailure(e);
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (sessionId) api.deleteSession(sessionId).catch(() => undefined);
    setItems([]);
    setPending([]);
    setDecisions({});
    await startSession();
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  const remaining = pending.filter((p) => decisions[p.action_id] === undefined).length;

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>Email Manager</h1>
          <p className="sub">{status}</p>
        </div>
        <button type="button" className="btn ghost" onClick={reset} disabled={busy}>New conversation</button>
      </header>

      <main className="chat" aria-live="polite">
        {items.length === 0 && (
          <div className="empty">
            <p>Ask in plain language. Anything that changes your mail, calendar or Drive will wait for your approval.</p>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} type="button" className="chip" onClick={() => send(ex)} disabled={busy}>{ex}</button>
              ))}
            </div>
          </div>
        )}

        {items.map((item) => <Message key={item.id} item={item} />)}

        {pending.length > 0 && (
          <div className="pending">
            {pending.map((p) => (
              <ApprovalCard key={p.action_id} action={p} decision={decisions[p.action_id]} disabled={busy}
                onDecide={(approve) => decide(p.action_id, approve)} />
            ))}
            {pending.length > 1 && remaining > 0 && (
              <p className="hint">Decide on {remaining} more action{remaining === 1 ? "" : "s"} to continue.</p>
            )}
          </div>
        )}

        {busy && <div className="msg assistant thinking"><span className="dot" /><span className="dot" /><span className="dot" /></div>}
        <div ref={endRef} />
      </main>

      <form className="composer" onSubmit={onSubmit}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
          placeholder={pending.length ? "Approve or reject the actions above first" : "e.g. Find emails from Sam this week and summarize them"}
          disabled={busy || pending.length > 0}
          rows={2}
          aria-label="Message"
        />
        <button type="submit" className="btn approve" disabled={busy || pending.length > 0 || !input.trim()}>Send</button>
      </form>
    </div>
  );
}

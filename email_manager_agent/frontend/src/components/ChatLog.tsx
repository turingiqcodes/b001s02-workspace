import { useEffect, useRef } from "react";
import type { ChatItem } from "../state/reducer";

const STATUS_LABEL: Record<string, string> = {
  ok: "done",
  error: "failed",
  invalid: "invalid input",
  pending: "needs approval",
};

export function ChatLog({ items, busy }: { items: ChatItem[]; busy: boolean }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [items.length, busy]);

  return (
    <div className="chat-log" aria-live="polite" aria-label="Conversation">
      {items.length === 0 && !busy && (
        <p className="chat-empty">Ask about your email, calendar or Drive. Nothing changes until you approve it.</p>
      )}
      {items.map((item) => (
        <article key={item.id} className={`chat-item chat-${item.kind}`}>
          <span className="visually-hidden">
            {item.kind === "user" ? "You" : item.kind === "assistant" ? "Assistant" : item.kind === "error" ? "Error" : "Notice"}:
          </span>
          <div className="chat-text">{item.text}</div>
          {item.toolCalls && item.toolCalls.length > 0 && (
            <ul className="tool-calls" aria-label="Steps taken">
              {item.toolCalls.map((tc, i) => (
                <li key={`${tc.name}-${i}`} className={`tool-call tool-${tc.status}`}>
                  {tc.summary || tc.name} <span className="tool-status">({STATUS_LABEL[tc.status] ?? tc.status})</span>
                </li>
              ))}
            </ul>
          )}
        </article>
      ))}
      {busy && (
        <div className="chat-item chat-thinking" role="status">
          Working… local models can take a while.
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}

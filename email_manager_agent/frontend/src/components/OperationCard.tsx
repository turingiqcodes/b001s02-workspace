import { useEffect, useState } from "react";
import type { Decision, PendingOperation } from "../types/api";

interface Props {
  op: PendingOperation;
  busy: boolean;
  error: string | undefined;
  onDecide: (operationId: string, decision: Decision, acknowledge: boolean) => void;
  onExpire: (operationId: string) => void;
  now?: () => number;
}

function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function OperationCard({ op, busy, error, onDecide, onExpire, now = Date.now }: Props) {
  const [acknowledged, setAcknowledged] = useState(false);
  const expiresAt = Date.parse(op.expires_at);
  const [remaining, setRemaining] = useState(() => expiresAt - now());

  // A new warning (e.g. a fresh calendar conflict) must be acknowledged again.
  const warningKey = op.warnings.join("\n");
  useEffect(() => {
    setAcknowledged(false);
  }, [warningKey]);

  useEffect(() => {
    const tick = () => {
      const left = expiresAt - now();
      setRemaining(left);
      if (left <= 0) onExpire(op.operation_id);
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [expiresAt, now, onExpire, op.operation_id]);

  const needsAck = op.requires_acknowledgement || op.warnings.length > 0;
  const canApprove = !busy && (!needsAck || acknowledged) && remaining > 0;
  const titleId = `op-title-${op.operation_id}`;

  return (
    <article className="op-card" aria-labelledby={titleId}>
      <header className="op-header">
        <h3 id={titleId}>{op.title}</h3>
        <span className="op-expiry" aria-label="Time left to approve">
          Expires in {formatRemaining(remaining)}
        </span>
      </header>
      <p className="op-summary">{op.summary}</p>
      {op.details.length > 0 && (
        <dl className="op-details">
          {op.details.map((d, i) => (
            <div key={`${d.label}-${i}`} className="op-detail">
              <dt>{d.label}</dt>
              <dd>{d.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {op.body && <pre className="op-body">{op.body}</pre>}
      {op.warnings.length > 0 && (
        <div className="op-warnings" role="alert">
          <strong>Warnings</strong>
          <ul>
            {op.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
          <label className="op-ack">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              disabled={busy}
            />
            I have reviewed these warnings
          </label>
        </div>
      )}
      {error && (
        <p className="op-error" role="alert">
          {error}
        </p>
      )}
      <div className="op-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!canApprove}
          onClick={() => onDecide(op.operation_id, "approve", acknowledged)}
        >
          {busy ? "Working…" : "Approve"}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={busy}
          onClick={() => onDecide(op.operation_id, "reject", false)}
        >
          Reject
        </button>
      </div>
    </article>
  );
}

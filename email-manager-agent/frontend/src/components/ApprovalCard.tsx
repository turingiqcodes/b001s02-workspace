import type { PendingAction } from "../types";

interface Props {
  action: PendingAction;
  decision: boolean | undefined;
  disabled: boolean;
  onDecide: (approve: boolean) => void;
}

export function ApprovalCard({ action, decision, disabled, onDecide }: Props) {
  return (
    <section className={`card approval ${decision === true ? "is-approved" : decision === false ? "is-rejected" : ""}`}
      aria-label={`Approval needed: ${action.title}`}>
      <header className="card-head">
        <span className="badge">Needs approval</span>
        <h3>{action.title}</h3>
      </header>

      <dl className="fields">
        {action.fields.map((f) => (
          <div key={f.label} className="field">
            <dt>{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>

      {action.body && <pre className="preview-body">{action.body}</pre>}
      {action.warning && <p className="warning" role="note">⚠ {action.warning}</p>}

      <div className="actions">
        <button type="button" className="btn reject" disabled={disabled} aria-pressed={decision === false}
          onClick={() => onDecide(false)}>
          Reject
        </button>
        <button type="button" className="btn approve" disabled={disabled} aria-pressed={decision === true}
          onClick={() => onDecide(true)}>
          Approve
        </button>
      </div>
    </section>
  );
}

import type { PendingAction } from '../types';

type Props = {
  action: PendingAction;
  busy: boolean;
  onDecide: (actionId: string, approved: boolean) => void;
};

const LABELS: Record<string, string> = {
  to: 'To',
  cc: 'Cc',
  bcc: 'Bcc',
  subject: 'Subject',
  body: 'Message',
  when: 'When',
  duration: 'Duration',
  title: 'Title',
  location: 'Location',
  description: 'Description',
  attendees: 'Attendees',
  labels: 'Labels',
  location_: 'Location',
  folder: 'Folder',
  files: 'Files',
  file: 'File',
  email: 'Email',
  event: 'Event',
  from: 'From',
  date: 'Date',
  name: 'Name',
  type: 'Type',
  size: 'Size',
  updated: 'Last updated',
  url: 'Link',
};

const HIDDEN = new Set(['message_id', 'draft_id']);

function label(key: string) {
  return LABELS[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.join(', ') || '—';
  return String(value);
}

function Fields({ data }: { data: Record<string, unknown> }) {
  const keys = Object.keys(data).filter((k) => !HIDDEN.has(k));
  if (!keys.length) return null;
  return (
    <dl className="fields">
      {keys.map((k) => (
        <div key={k} className="field">
          <dt>{label(k)}</dt>
          <dd className={k === 'body' || k === 'description' ? 'pre' : undefined}>{show(data[k])}</dd>
        </div>
      ))}
    </dl>
  );
}

function ChangeTable({ before, after }: { before: Record<string, unknown>; after: Record<string, unknown> }) {
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).filter((k) => !HIDDEN.has(k));
  return (
    <table className="changes">
      <thead>
        <tr>
          <th scope="col"></th>
          <th scope="col">Current</th>
          <th scope="col">Proposed</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k}>
            <th scope="row">{label(k)}</th>
            <td className="pre old">{k in before ? show(before[k]) : '(unchanged)'}</td>
            <td className="pre new">{k in after ? show(after[k]) : '(unchanged)'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ApprovalCard({ action, busy, onDecide }: Props) {
  const p = action.preview;
  const pending = action.status === 'needs_confirmation';
  const statusText: Record<string, string> = {
    executed: 'Done',
    declined: 'Cancelled',
    failed: 'Failed',
  };

  return (
    <article className={`card ${p.irreversible ? 'card-danger' : ''} card-${action.status}`} aria-label={p.title}>
      <header className="card-head">
        <h3>{p.title}</h3>
        {!pending && <span className={`badge badge-${action.status}`}>{statusText[action.status]}</span>}
        {pending && <span className="badge badge-pending">Needs your approval</span>}
      </header>
      <p className="summary">{p.summary}</p>

      {p.target && (
        <section>
          <h4>Applies to</h4>
          <Fields data={p.target} />
        </section>
      )}

      {p.existing && p.proposed ? (
        <section>
          <h4>Changes</h4>
          <ChangeTable before={p.existing} after={p.proposed} />
        </section>
      ) : (
        <>
          {p.existing && (
            <section>
              <h4>Current</h4>
              <Fields data={p.existing} />
            </section>
          )}
          {p.proposed && (
            <section>
              <h4>Proposed</h4>
              <Fields data={p.proposed} />
            </section>
          )}
        </>
      )}

      {p.availability && (
        <section>
          <h4>Availability</h4>
          {p.availability.free ? (
            <p className="ok-text">You're free at this time.</p>
          ) : (
            <ul className="warn-list">
              {p.availability.conflicts.map((c) => (
                <li key={c}>Overlaps: {c}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {p.notify_attendees !== null && (
        <p className="notify">
          {p.notify_attendees ? '✉ Attendees will be notified by email.' : 'Attendees will not be notified.'}
        </p>
      )}

      {p.warnings.length > 0 && (
        <ul className="warn-list" role="note">
          {p.warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      )}

      {pending ? (
        <div className="card-actions">
          <button
            type="button"
            className={`btn ${p.irreversible ? 'btn-danger' : 'btn-primary'}`}
            disabled={busy}
            onClick={() => onDecide(action.action_id, true)}
          >
            {p.irreversible ? "Approve (can't be undone)" : 'Approve'}
          </button>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => onDecide(action.action_id, false)}>
            Cancel
          </button>
        </div>
      ) : (
        action.message && <p className={`result result-${action.status} pre`}>{action.message}</p>
      )}
    </article>
  );
}

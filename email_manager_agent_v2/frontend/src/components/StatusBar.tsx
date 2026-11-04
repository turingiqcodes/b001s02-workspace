import type { Status } from '../types';

type Props = {
  status: Status | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onReset: () => void;
  resetDisabled: boolean;
};

function Pill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`pill ${ok ? 'pill-ok' : 'pill-bad'}`}>
      <span aria-hidden="true">{ok ? '●' : '○'}</span> {label}
    </span>
  );
}

export function StatusBar({ status, loading, error, onRefresh, onReset, resetDisabled }: Props) {
  return (
    <header className="status-bar">
      <div className="brand">Email Manager</div>
      <div className="pills" aria-live="polite">
        {loading && !status && <span className="muted">Checking connection…</span>}
        {error && <span className="pill pill-bad">Backend offline</span>}
        {status && (
          <>
            <Pill ok={status.llm_configured} label={`AI · ${status.llm_model}`} />
            <Pill ok={status.bridge_configured} label="Bridge" />
            <Pill ok={status.gmail_connected} label={status.gmail_address ?? 'Gmail not connected'} />
          </>
        )}
      </div>
      <div className="bar-actions">
        <button type="button" className="btn btn-ghost" onClick={onRefresh} disabled={loading}>
          {loading ? 'Checking…' : 'Refresh'}
        </button>
        <button type="button" className="btn btn-ghost" onClick={onReset} disabled={resetDisabled}>
          Reset session
        </button>
      </div>
      {(error || (status && status.status !== 'ready' && status.detail)) && (
        <div className="status-detail">{error ?? status?.detail}</div>
      )}
    </header>
  );
}

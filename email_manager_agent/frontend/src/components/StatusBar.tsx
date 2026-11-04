import type { ServiceStatus, StatusResponse } from "../types/api";

interface Props {
  status: StatusResponse | null;
  error: string | null;
  checking: boolean;
  onCheck: () => void;
}

type Level = "ok" | "warn" | "bad" | "unknown";

function levelOf(s: ServiceStatus): Level {
  if (!s.configured || s.reachable === false) return "bad";
  if (s.reachable === true) return "ok";
  return s.issues.length ? "warn" : "unknown";
}

function describe(s: ServiceStatus, configuredText: string): string {
  if (!s.configured) return "Not configured";
  if (s.reachable === true) return "Connected";
  if (s.reachable === false) return "Unreachable";
  return configuredText;
}

function Item({ label, value, level, notes }: { label: string; value: string; level: Level; notes: string[] }) {
  return (
    <div className={`status-item status-${level}`}>
      <span className="status-dot" aria-hidden="true" />
      <span className="status-label">{label}:</span> <span className="status-value">{value}</span>
      {notes.length > 0 && (
        <ul className="status-notes">
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function StatusBar({ status, error, checking, onCheck }: Props) {
  if (!status) {
    return (
      <section className="status-bar" aria-label="Connection status" role="status">
        <Item label="Backend" value={error ? "Backend offline" : "Loading…"} level={error ? "bad" : "unknown"} notes={error ? [error] : []} />
      </section>
    );
  }
  const { llm, bridge } = status;
  const providerName = llm.provider === "claude" ? "Claude" : llm.provider === "ollama" ? "Ollama" : llm.provider;
  const modelLabel = `${providerName}${llm.model ? ` · ${llm.model}` : ""}`;
  const llmNotes = [...llm.issues, ...(llm.detail ? [llm.detail] : [])];
  const bridgeNotes = [...bridge.issues, ...(bridge.detail ? [bridge.detail] : [])];

  return (
    <section className="status-bar" aria-label="Connection status" role="status">
      <Item label="Model" value={`${modelLabel} — ${describe(llm, "Configured")}`} level={levelOf(llm)} notes={llmNotes} />
      <Item label="Google" value={describe(bridge, "Configured")} level={levelOf(bridge)} notes={bridgeNotes} />
      {error && <Item label="Backend" value="Backend offline" level="bad" notes={[error]} />}
      <span className="status-tz">Timezone: {status.timezone}</span>
      <button type="button" className="btn btn-secondary" onClick={onCheck} disabled={checking}>
        {checking ? "Checking…" : "Check connection"}
      </button>
    </section>
  );
}

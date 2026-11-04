import type { ChatItem } from "../types";

const STATUS_TEXT = { done: "Done", rejected: "Rejected", failed: "Failed" } as const;

export function Message({ item }: { item: ChatItem }) {
  if (item.kind === "user") {
    return <div className="msg user"><p>{item.text}</p></div>;
  }
  if (item.kind === "error") {
    return <div className="msg error" role="alert"><p>{item.text}</p></div>;
  }
  return (
    <div className="msg assistant">
      {item.executed.length > 0 && (
        <ul className="executed">
          {item.executed.map((e) => (
            <li key={e.action_id} className={`status-${e.status}`}>
              <strong>{STATUS_TEXT[e.status]}:</strong> {e.title}
              {e.detail && (e.detail.startsWith("https://")
                ? <> — <a href={e.detail} target="_blank" rel="noreferrer">open</a></>
                : <> — {e.detail}</>)}
            </li>
          ))}
        </ul>
      )}
      {item.activity.length > 0 && (
        <p className="activity">
          {item.activity.map((a, i) => (
            <span key={i} className={a.ok ? "" : "activity-failed"}>{a.label}{a.ok ? "" : " (failed)"}</span>
          ))}
        </p>
      )}
      {item.text && <p className="text">{item.text}</p>}
    </div>
  );
}

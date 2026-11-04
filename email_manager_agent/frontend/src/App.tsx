import { useState } from "react";
import { ChatLog } from "./components/ChatLog";
import { ExamplePrompts } from "./components/ExamplePrompts";
import { PendingOperations } from "./components/PendingOperations";
import { PromptInput } from "./components/PromptInput";
import { StatusBar } from "./components/StatusBar";
import { useAgent } from "./state/useAgent";

const DEFAULT_MAX_CHARS = 4000;

export default function App() {
  const { state, refreshStatus, submitPrompt, decide, expire, newConversation } = useAgent();
  const [draft, setDraft] = useState("");
  const maxChars = state.status?.limits.max_prompt_chars ?? DEFAULT_MAX_CHARS;

  return (
    <div className="app">
      <header className="app-header">
        <h1>Email Manager</h1>
        <button type="button" className="btn btn-secondary" onClick={() => void newConversation()} disabled={state.busy}>
          New conversation
        </button>
      </header>
      <StatusBar
        status={state.status}
        error={state.statusError}
        checking={state.checking}
        onCheck={() => void refreshStatus(true)}
      />
      <main className="app-main">
        <ChatLog items={state.items} busy={state.busy} />
        <PendingOperations
          pending={state.pending}
          opBusy={state.opBusy}
          opErrors={state.opErrors}
          onDecide={(id, d, ack) => void decide(id, d, ack)}
          onExpire={expire}
        />
        {state.items.length === 0 && <ExamplePrompts onPick={setDraft} disabled={state.busy} />}
        <PromptInput
          maxChars={maxChars}
          disabled={state.busy}
          value={draft}
          onChange={setDraft}
          onSubmit={(t) => void submitPrompt(t)}
        />
      </main>
    </div>
  );
}

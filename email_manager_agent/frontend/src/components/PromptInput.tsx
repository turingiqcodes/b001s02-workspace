import { useState, type FormEvent, type KeyboardEvent } from "react";

interface Props {
  maxChars: number;
  disabled: boolean;
  onSubmit: (text: string) => void;
  value?: string;
  onChange?: (text: string) => void;
}

export function PromptInput({ maxChars, disabled, onSubmit, value, onChange }: Props) {
  const [inner, setInner] = useState("");
  const text = value ?? inner;
  const setText = onChange ?? setInner;
  const tooLong = text.length > maxChars;
  const empty = text.trim().length === 0;

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (disabled || empty || tooLong) return;
    onSubmit(text.trim());
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form className="prompt-form" onSubmit={submit}>
      <label htmlFor="prompt" className="visually-hidden">
        Your request
      </label>
      <textarea
        id="prompt"
        rows={3}
        value={text}
        placeholder="e.g. Find unread emails from today"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={disabled}
        aria-describedby="prompt-count"
        aria-invalid={tooLong}
      />
      <div className="prompt-footer">
        <span id="prompt-count" className={tooLong ? "count count-over" : "count"}>
          {text.length}/{maxChars}
        </span>
        <button type="submit" className="btn btn-primary" disabled={disabled || empty || tooLong}>
          Send
        </button>
      </div>
    </form>
  );
}

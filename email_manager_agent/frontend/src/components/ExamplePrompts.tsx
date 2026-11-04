export const EXAMPLE_PROMPTS = [
  "Find unread emails from today",
  "Summarise the latest email from Alice",
  "Draft a reply to that email saying I'll send the report Friday",
  "Find a free 45 minute slot on Tuesday or Wednesday",
  'Schedule "Planning" with bob@example.com at the first free slot',
  "Move my 3pm meeting tomorrow to 4pm",
  'Find PDFs with "invoice" in the name',
  "Save the attachments from the latest invoice email to Drive",
];

export function ExamplePrompts({ onPick, disabled }: { onPick: (text: string) => void; disabled: boolean }) {
  return (
    <div className="examples" aria-label="Example requests">
      {EXAMPLE_PROMPTS.map((p) => (
        <button key={p} type="button" className="chip" onClick={() => onPick(p)} disabled={disabled}>
          {p}
        </button>
      ))}
    </div>
  );
}

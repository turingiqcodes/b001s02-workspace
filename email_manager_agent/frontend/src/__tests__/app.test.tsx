import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { errorResponse, jsonResponse, makeOp, makePromptResponse, makeStatus, mockFetch } from "./fixtures";

async function ask(text: string) {
  await userEvent.type(screen.getByLabelText("Your request"), `${text}{Enter}`);
}

describe("App approval flow", () => {
  it("sends a prompt and shows the reply and tool steps", async () => {
    const calls = mockFetch({
      "GET /api/status": () => jsonResponse(makeStatus()),
      "POST /api/prompt": () => jsonResponse(makePromptResponse({ reply: "You have 2 unread emails." })),
    });
    render(<App />);
    await screen.findByText(/Ollama · qwen3:8b/);
    await ask("Find unread emails from today");
    expect(await screen.findByText("You have 2 unread emails.")).toBeInTheDocument();
    expect(screen.getByText(/Searched email/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ prompt: "Find unread emails from today" });
  });

  it("approves a pending operation and shows the result", async () => {
    const op = makeOp();
    const calls = mockFetch({
      "GET /api/status": () => jsonResponse(makeStatus()),
      "POST /api/prompt": () => jsonResponse(makePromptResponse({ reply: "Ready to send.", pending_operations: [op] })),
      "POST /api/confirm": () =>
        jsonResponse({ status: "executed", message: "Email sent.", result: { message_id: "m1" }, pending_operations: [] }),
    });
    render(<App />);
    await ask("Send a test email");
    expect(await screen.findByRole("heading", { name: "Waiting for your approval" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Email sent.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Waiting for your approval" })).not.toBeInTheDocument();
    const confirm = calls.find((c) => c.url === "/api/confirm");
    expect(confirm?.body).toEqual({ session_id: "s1", operation_id: op.operation_id, decision: "approve", acknowledge_warnings: false });
  });

  it("reloads warnings when the backend requires a new acknowledgement", async () => {
    const op = makeOp({ tool: "create_event", title: "Create event" });
    const updated = { ...op, warnings: ["Now conflicts with Lunch"], requires_acknowledgement: true };
    mockFetch({
      "GET /api/status": [
        () => jsonResponse(makeStatus()),
        () => jsonResponse(makeStatus({ session_id: "s1", pending_operations: [updated] })),
      ],
      "POST /api/prompt": () => jsonResponse(makePromptResponse({ pending_operations: [op] })),
      "POST /api/confirm": () => errorResponse("acknowledgement_required", "Review the new warnings first.", 409),
    });
    render(<App />);
    await ask("Schedule planning");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Review the new warnings first.")).toBeInTheDocument();
    expect(await screen.findByText("Now conflicts with Lunch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("removes an expired approval", async () => {
    mockFetch({
      "GET /api/status": () => jsonResponse(makeStatus()),
      "POST /api/prompt": () => jsonResponse(makePromptResponse({ pending_operations: [makeOp()] })),
      "POST /api/confirm": () => errorResponse("approval_expired", "This approval expired. Ask again.", 410),
    });
    render(<App />);
    await ask("Send it");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("This approval expired. Ask again.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("starts over when the session no longer exists", async () => {
    const calls = mockFetch({
      "GET /api/status": () => jsonResponse(makeStatus()),
      "POST /api/prompt": [
        () => jsonResponse(makePromptResponse()),
        () => errorResponse("not_found", "This session no longer exists. Send your request again.", 404),
        () => jsonResponse(makePromptResponse({ session_id: "s2", reply: "Fresh start." })),
      ],
    });
    render(<App />);
    await ask("one");
    await screen.findByText("Here you go.");
    await ask("two");
    expect(await screen.findByText("This session no longer exists. Send your request again.")).toBeInTheDocument();
    await ask("three");
    await screen.findByText("Fresh start.");
    const prompts = calls.filter((c) => c.url === "/api/prompt").map((c) => c.body);
    expect(prompts[1]).toEqual({ prompt: "two", session_id: "s1" });
    expect(prompts[2]).toEqual({ prompt: "three" });
  });

  it("New conversation resets the session", async () => {
    const calls = mockFetch({
      "GET /api/status": () => jsonResponse(makeStatus()),
      "POST /api/prompt": () => jsonResponse(makePromptResponse({ pending_operations: [makeOp()] })),
      "POST /api/reset": () => jsonResponse({ session_id: "s9", status: "reset" }),
    });
    render(<App />);
    await ask("hello");
    await screen.findByText("Here you go.");
    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    await waitFor(() => expect(screen.queryByText("Here you go.")).not.toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "Waiting for your approval" })).not.toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/reset")?.body).toEqual({ session_id: "s1" });
  });

  it("example chips fill the prompt box", async () => {
    mockFetch({ "GET /api/status": () => jsonResponse(makeStatus()) });
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Find unread emails from today" }));
    expect(screen.getByLabelText("Your request")).toHaveValue("Find unread emails from today");
  });

  it("Check connection requests a bridge check", async () => {
    const calls = mockFetch({
      "GET /api/status": [
        () => jsonResponse(makeStatus()),
        () =>
          jsonResponse(
            makeStatus({ bridge: { configured: true, issues: [], reachable: false, detail: "Invalid shared secret" } }),
          ),
      ],
    });
    render(<App />);
    await screen.findByText(/Ollama · qwen3:8b/);
    await userEvent.click(screen.getByRole("button", { name: "Check connection" }));
    expect(await screen.findByText("Invalid shared secret")).toBeInTheDocument();
    expect(calls[1].url).toBe("/api/status?check_bridge=true");
  });
});

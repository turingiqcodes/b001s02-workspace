import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OperationCard } from "../components/OperationCard";
import { PromptInput } from "../components/PromptInput";
import { StatusBar } from "../components/StatusBar";
import { makeOp, makeStatus } from "./fixtures";

describe("StatusBar", () => {
  it("shows model, bridge issues and the check button", async () => {
    const onCheck = vi.fn();
    render(
      <StatusBar
        status={makeStatus({
          bridge: { configured: false, issues: ["APPS_SCRIPT_URL is not set."], reachable: null, detail: null },
        })}
        error={null}
        checking={false}
        onCheck={onCheck}
      />,
    );
    expect(screen.getByText(/Ollama · qwen3:8b/)).toBeInTheDocument();
    expect(screen.getByText("APPS_SCRIPT_URL is not set.")).toBeInTheDocument();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check connection" }));
    expect(onCheck).toHaveBeenCalledOnce();
  });

  it("shows backend offline when status failed to load", () => {
    render(<StatusBar status={null} error="Backend offline. Start it." checking={false} onCheck={() => {}} />);
    expect(screen.getByText("Backend offline")).toBeInTheDocument();
  });
});

describe("PromptInput", () => {
  it("enforces the character limit", async () => {
    const onSubmit = vi.fn();
    render(<PromptInput maxChars={5} disabled={false} onSubmit={onSubmit} />);
    const box = screen.getByLabelText("Your request");
    await userEvent.type(box, "123456");
    expect(screen.getByText("6/5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("submits on Enter and clears", async () => {
    const onSubmit = vi.fn();
    render(<PromptInput maxChars={100} disabled={false} onSubmit={onSubmit} />);
    const box = screen.getByLabelText("Your request");
    await userEvent.type(box, "  hello {Enter}");
    expect(onSubmit).toHaveBeenCalledWith("hello");
    expect(box).toHaveValue("");
  });
});

describe("OperationCard", () => {
  it("requires acknowledging warnings before approving", async () => {
    const onDecide = vi.fn();
    const op = makeOp({ warnings: ["Conflicts with Standup"], requires_acknowledgement: true });
    render(<OperationCard op={op} busy={false} error={undefined} onDecide={onDecide} onExpire={() => {}} />);
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByLabelText("I have reviewed these warnings"));
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    expect(onDecide).toHaveBeenCalledWith(op.operation_id, "approve", true);
  });

  it("shows details and body and allows rejecting", async () => {
    const onDecide = vi.fn();
    const op = makeOp();
    render(<OperationCard op={op} busy={false} error={undefined} onDecide={onDecide} onExpire={() => {}} />);
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getByText("Hi Alice")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onDecide).toHaveBeenCalledWith(op.operation_id, "reject", false);
  });

  it("counts down and calls onExpire", () => {
    vi.useFakeTimers();
    const start = Date.now();
    let now = start;
    const onExpire = vi.fn();
    const op = makeOp({ expires_at: new Date(start + 2_000).toISOString() });
    render(<OperationCard op={op} busy={false} error={undefined} onDecide={() => {}} onExpire={onExpire} now={() => now} />);
    expect(screen.getByText("Expires in 0:02")).toBeInTheDocument();
    act(() => {
      now = start + 2_500;
      vi.advanceTimersByTime(1000);
    });
    expect(onExpire).toHaveBeenCalledWith(op.operation_id);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });
});

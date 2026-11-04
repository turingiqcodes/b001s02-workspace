import { describe, expect, it } from "vitest";
import { initialState, reducer, type AppState } from "../state/reducer";
import { makeOp, makePromptResponse, makeStatus } from "./fixtures";

function withSession(): AppState {
  return reducer(reducer(initialState, { type: "prompt_started", text: "hi" }), {
    type: "prompt_succeeded",
    response: makePromptResponse({ pending_operations: [makeOp()] }),
  });
}

describe("reducer", () => {
  it("adds the user message and marks busy", () => {
    const s = reducer(initialState, { type: "prompt_started", text: "Find unread" });
    expect(s.busy).toBe(true);
    expect(s.items).toHaveLength(1);
    expect(s.items[0]).toMatchObject({ kind: "user", text: "Find unread" });
  });

  it("stores the session, reply and pending operations", () => {
    const s = withSession();
    expect(s.busy).toBe(false);
    expect(s.sessionId).toBe("s1");
    expect(s.items[1]).toMatchObject({ kind: "assistant", text: "Here you go." });
    expect(s.pending).toHaveLength(1);
  });

  it("adds a notice when the step limit is reached", () => {
    const s = reducer(initialState, {
      type: "prompt_succeeded",
      response: makePromptResponse({ stop_reason: "max_steps" }),
    });
    expect(s.items.at(-1)).toMatchObject({ kind: "notice" });
  });

  it("clears the session when it is lost", () => {
    const s = reducer(withSession(), { type: "session_lost", message: "This session no longer exists." });
    expect(s.sessionId).toBeNull();
    expect(s.pending).toEqual([]);
    expect(s.items.at(-1)).toMatchObject({ kind: "error", text: "This session no longer exists." });
  });

  it("keeps an op pending with its error when acknowledgement is required", () => {
    const opId = makeOp().operation_id;
    let s = reducer(withSession(), { type: "confirm_started", operationId: opId });
    expect(s.opBusy[opId]).toBe(true);
    s = reducer(s, { type: "confirm_failed", operationId: opId, message: "Review the warnings.", remove: false });
    expect(s.pending).toHaveLength(1);
    expect(s.opErrors[opId]).toBe("Review the warnings.");
    expect(s.opBusy[opId]).toBeUndefined();
  });

  it("removes an expired op", () => {
    const opId = makeOp().operation_id;
    const s = reducer(withSession(), { type: "operation_expired", operationId: opId });
    expect(s.pending).toEqual([]);
  });

  it("uses status pending ops only for the current session", () => {
    const base = withSession();
    const other = reducer(base, { type: "status_loaded", status: makeStatus({ session_id: "other", pending_operations: [] }) });
    expect(other.pending).toHaveLength(1);
    const same = reducer(base, { type: "status_loaded", status: makeStatus({ session_id: "s1", pending_operations: [] }) });
    expect(same.pending).toHaveLength(0);
  });

  it("reset clears the conversation but keeps status", () => {
    const status = makeStatus();
    const s0 = reducer(withSession(), { type: "status_loaded", status });
    const s = reducer(s0, { type: "reset", sessionId: "new" });
    expect(s.items).toEqual([]);
    expect(s.pending).toEqual([]);
    expect(s.sessionId).toBe("new");
    expect(s.status).toBe(status);
  });
});

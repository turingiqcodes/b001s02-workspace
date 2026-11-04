import { describe, expect, it } from "vitest";
import { ApiError, confirmOperation, getStatus, OFFLINE_MESSAGE, resetSession, sendPrompt } from "../api/client";
import { errorResponse, jsonResponse, makePromptResponse, makeStatus, mockFetch } from "./fixtures";

describe("api client", () => {
  it("builds the status query string", async () => {
    const calls = mockFetch({ "GET /api/status": () => jsonResponse(makeStatus()) });
    await getStatus("abc", true);
    await getStatus(null, false);
    expect(calls[0].url).toBe("/api/status?session_id=abc&check_bridge=true");
    expect(calls[1].url).toBe("/api/status");
  });

  it("omits session_id from a prompt when there is none", async () => {
    const calls = mockFetch({ "POST /api/prompt": () => jsonResponse(makePromptResponse()) });
    await sendPrompt(null, "hi");
    await sendPrompt("s1", "again");
    expect(calls[0].body).toEqual({ prompt: "hi" });
    expect(calls[1].body).toEqual({ prompt: "again", session_id: "s1" });
  });

  it("parses the backend error envelope", async () => {
    mockFetch({ "POST /api/prompt": () => errorResponse("session_busy", "Another request is running.", 409) });
    const err = await sendPrompt("s1", "x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("session_busy");
    expect(err.status).toBe(409);
    expect(err.message).toBe("Another request is running.");
  });

  it("reports a network failure as backend offline", async () => {
    globalThis.fetch = (() => Promise.reject(new TypeError("Failed to fetch"))) as typeof fetch;
    const err = await getStatus(null).catch((e) => e);
    expect(err.code).toBe("backend_offline");
    expect(err.message).toBe(OFFLINE_MESSAGE);
  });

  it("treats a non-JSON proxy error as backend offline", async () => {
    mockFetch({ "GET /api/status": () => new Response("Bad gateway", { status: 502 }) });
    const err = await getStatus(null).catch((e) => e);
    expect(err.code).toBe("backend_offline");
  });

  it("sends confirm and reset bodies", async () => {
    const calls = mockFetch({
      "POST /api/confirm": () => jsonResponse({ status: "rejected", message: "Cancelled.", result: null, pending_operations: [] }),
      "POST /api/reset": () => jsonResponse({ session_id: "new", status: "reset" }),
    });
    await confirmOperation({ session_id: "s1", operation_id: "op_1", decision: "reject", acknowledge_warnings: false });
    const r = await resetSession(null);
    expect(calls[0].body).toEqual({ session_id: "s1", operation_id: "op_1", decision: "reject", acknowledge_warnings: false });
    expect(calls[1].body).toEqual({});
    expect(r.session_id).toBe("new");
  });
});

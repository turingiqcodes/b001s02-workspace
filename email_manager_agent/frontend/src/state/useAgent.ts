import { useCallback, useEffect, useReducer, useRef } from "react";
import { ApiError, confirmOperation, getStatus, resetSession, sendPrompt } from "../api/client";
import type { Decision } from "../types/api";
import { initialState, reducer } from "./reducer";

function messageOf(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Something went wrong. Please try again.";
}

export function useAgent() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const sessionRef = useRef<string | null>(null);
  sessionRef.current = state.sessionId;

  const refreshStatus = useCallback(async (checkBridge = false) => {
    if (checkBridge) dispatch({ type: "check_started" });
    try {
      const status = await getStatus(sessionRef.current, checkBridge);
      dispatch({ type: "status_loaded", status });
    } catch (err) {
      dispatch({ type: "status_failed", message: messageOf(err) });
    } finally {
      if (checkBridge) dispatch({ type: "check_finished" });
    }
  }, []);

  useEffect(() => {
    void refreshStatus(false);
  }, [refreshStatus]);

  const submitPrompt = useCallback(async (text: string) => {
    const prompt = text.trim();
    if (!prompt) return;
    dispatch({ type: "prompt_started", text: prompt });
    try {
      const response = await sendPrompt(sessionRef.current, prompt);
      dispatch({ type: "prompt_succeeded", response });
    } catch (err) {
      if (err instanceof ApiError && err.code === "not_found") {
        dispatch({ type: "session_lost", message: err.message });
      } else {
        dispatch({ type: "prompt_failed", message: messageOf(err) });
      }
    }
  }, []);

  const decide = useCallback(
    async (operationId: string, decision: Decision, acknowledgeWarnings: boolean) => {
      const sessionId = sessionRef.current;
      if (!sessionId) return;
      dispatch({ type: "confirm_started", operationId });
      try {
        const response = await confirmOperation({
          session_id: sessionId,
          operation_id: operationId,
          decision,
          acknowledge_warnings: acknowledgeWarnings,
        });
        dispatch({ type: "confirm_succeeded", operationId, response });
      } catch (err) {
        const message = messageOf(err);
        if (err instanceof ApiError) {
          if (err.code === "acknowledgement_required") {
            dispatch({ type: "confirm_failed", operationId, message, remove: false });
            // The backend may have added new warnings (e.g. a new calendar conflict). Reload them.
            try {
              const status = await getStatus(sessionId, false);
              dispatch({ type: "pending_replaced", pending: status.pending_operations });
            } catch {
              /* keep the current card; the error is already shown */
            }
            return;
          }
          if (err.code === "approval_expired" || err.code === "approval_error") {
            dispatch({ type: "confirm_failed", operationId, message, remove: true });
            return;
          }
          if (err.code === "not_found" && /session/i.test(err.message)) {
            dispatch({ type: "session_lost", message });
            return;
          }
          if (err.code === "not_found") {
            dispatch({ type: "confirm_failed", operationId, message, remove: true });
            return;
          }
        }
        dispatch({ type: "confirm_failed", operationId, message, remove: false });
      }
    },
    [],
  );

  const expire = useCallback((operationId: string) => {
    dispatch({ type: "operation_expired", operationId });
  }, []);

  const newConversation = useCallback(async () => {
    try {
      const res = await resetSession(sessionRef.current);
      dispatch({ type: "reset", sessionId: res.session_id });
    } catch (err) {
      dispatch({ type: "reset", sessionId: null });
      dispatch({ type: "prompt_failed", message: messageOf(err) });
    }
  }, []);

  return { state, refreshStatus, submitPrompt, decide, expire, newConversation };
}

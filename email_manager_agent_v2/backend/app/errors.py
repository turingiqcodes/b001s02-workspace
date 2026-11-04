"""Error types. Every error carries a short code and a plain-language message
that is safe to show the user or hand back to the agent (NFR-13, NFR-22)."""

from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class BridgeError(AppError):
    """The Apps Script bridge failed or rejected a request."""

    status_code = 502
    code = "bridge_error"


class LLMError(AppError):
    """The AI model (Groq) could not be reached or rejected the request."""

    status_code = 502
    code = "llm_error"


class NotConfiguredError(AppError):
    status_code = 503
    code = "not_configured"


class SessionBusyError(AppError):
    status_code = 409
    code = "session_busy"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ActionStateError(AppError):
    status_code = 409
    code = "action_not_pending"


class PreviewRejected(AppError):
    """A write could not be turned into a preview (e.g. a calendar conflict).
    The message is returned to the agent so it can ask the user."""

    code = "preview_rejected"

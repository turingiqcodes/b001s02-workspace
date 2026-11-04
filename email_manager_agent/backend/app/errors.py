"""Application errors. Every error response has the shape ``{"error": {"code", "message"}}``."""

from __future__ import annotations


class AppError(Exception):
    code = "internal_error"
    status = 500

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status

    def to_body(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": self.code, "message": self.message}}


class ValidationAppError(AppError):
    code, status = "validation_error", 422


class NotFoundError(AppError):
    code, status = "not_found", 404


class ApprovalError(AppError):
    code, status = "approval_error", 409


class AcknowledgementRequired(AppError):
    code, status = "acknowledgement_required", 409


class SessionBusy(AppError):
    code, status = "session_busy", 409


class ApprovalExpired(AppError):
    code, status = "approval_expired", 410


class ConfigurationError(AppError):
    code, status = "configuration_error", 503


class BridgeError(AppError):
    """The Google Apps Script bridge failed or reported an error.

    ``bridge_code`` is the code the bridge itself returned (``unauthorized``,
    ``not_found`` ...) or a local one (``timeout``, ``unreachable``, ``bad_response``).
    """

    code, status = "bridge_error", 502

    def __init__(self, message: str, *, bridge_code: str = "internal"):
        timeout = bridge_code == "timeout"
        super().__init__(message, code="bridge_timeout" if timeout else "bridge_error", status=504 if timeout else 502)
        self.bridge_code = bridge_code


class ModelError(AppError):
    """The language model failed (connection, missing model, rate limit, timeout...)."""

    code, status = "model_error", 502


SESSION_GONE = "This session no longer exists. Send your request again."

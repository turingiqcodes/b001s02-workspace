"""Request/response models for the REST API (section 6.3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptRequest(_Req):
    prompt: str = Field(..., min_length=1, max_length=8000)
    session_id: str | None = Field(None, max_length=100)

    @field_validator("prompt")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must not be blank")
        return v.strip()


class ConfirmRequest(_Req):
    action_id: str = Field(..., min_length=1, max_length=100)
    approved: bool


class ResetRequest(_Req):
    session_id: str | None = Field(None, max_length=100)


class PendingActionOut(BaseModel):
    action_id: str
    operation: str
    status: str
    preview: dict[str, Any]
    created_at: str
    message: str | None = None


class PromptResponse(BaseModel):
    session_id: str
    response: str
    pending_actions: list[PendingActionOut]
    declined_actions: list[str] = Field(default_factory=list, description="Ids auto-declined by this prompt (FR-37).")


class ConfirmResponse(BaseModel):
    action_id: str
    status: Literal["executed", "declined", "failed"]
    message: str


class ResetResponse(BaseModel):
    status: Literal["reset"] = "reset"
    message: str = "Session cleared."


class StatusResponse(BaseModel):
    llm_configured: bool
    llm_model: str
    bridge_configured: bool
    gmail_connected: bool
    gmail_address: str | None
    time_zone: str | None
    status: Literal["ready", "not_configured", "bridge_error"]
    detail: str | None = None

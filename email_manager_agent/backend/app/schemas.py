"""Request and response models for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptIn(_Strict):
    session_id: str | None = Field(default=None, max_length=128)
    prompt: str = Field(max_length=200_000)


class ConfirmIn(_Strict):
    session_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "reject"]
    acknowledge_warnings: bool = False


class ResetIn(_Strict):
    session_id: str | None = Field(default=None, max_length=128)


class Detail(BaseModel):
    label: str
    value: str


class PendingOperationOut(BaseModel):
    operation_id: str
    tool: str
    title: str
    summary: str
    details: list[Detail]
    body: str | None
    warnings: list[str]
    requires_acknowledgement: bool
    created_at: str
    expires_at: str


class ToolCallOut(BaseModel):
    name: str
    status: Literal["ok", "error", "invalid", "pending"]
    summary: str
    operation_id: str | None = None


class PromptOut(BaseModel):
    session_id: str
    reply: str
    steps: int
    stop_reason: Literal["completed", "max_steps", "refusal", "max_tokens"]
    tool_calls: list[ToolCallOut]
    pending_operations: list[PendingOperationOut]


class ConfirmOut(BaseModel):
    status: Literal["executed", "rejected", "failed"]
    message: str
    result: Any = None
    pending_operations: list[PendingOperationOut]


class ResetOut(BaseModel):
    session_id: str
    status: Literal["reset"]


class ComponentStatus(BaseModel):
    configured: bool
    issues: list[str]
    reachable: bool | None = None
    detail: str | None = None


class LLMStatus(ComponentStatus):
    provider: str
    model: str | None


class StatusOut(BaseModel):
    llm: LLMStatus
    bridge: ComponentStatus
    timezone: str
    limits: dict[str, Any]
    session_id: str | None
    pending_operations: list[PendingOperationOut]

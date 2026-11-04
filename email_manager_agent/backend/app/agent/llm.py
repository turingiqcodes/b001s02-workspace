"""Provider-neutral model interface.

History format (list of dicts), shared by every provider:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}], "raw": <provider data>}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}
Each client converts this to its own wire format. ``raw`` lets a provider replay
its own assistant turn unchanged (Claude's thinking blocks must be passed back as-is).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Any  # usually a dict; a str when the model sent invalid JSON


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end"  # end | tool_use | max_tokens | refusal
    raw: Any = None


class LLMClient(Protocol):
    provider: str
    model: str

    def chat(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...

    def ping(self) -> tuple[bool | None, str | None]:
        """(reachable, detail). ``None`` means "not checked" for this provider."""
        ...

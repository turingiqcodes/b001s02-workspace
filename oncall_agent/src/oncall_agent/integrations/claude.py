"""Real Claude client (Anthropic Python SDK).

Uses structured outputs (``output_config.format`` with a JSON schema) so the
model is constrained to our analysis schema; we still validate the result
ourselves with Pydantic because the policy must never trust unvalidated data.

Server-side refusal fallback (``fallbacks: "default"``) is enabled so a
safety-classifier decline is retried on Anthropic's recommended fallback model
instead of failing the analysis.
"""
from __future__ import annotations

from typing import Any, Optional

import anthropic

from . import ClaudeClient, ClaudeUnavailable


class AnthropicClaudeClient(ClaudeClient):
    """Optional provider: ONCALL_LLM_PROVIDER=claude."""

    mode = "real"
    provider = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", timeout: float = 120.0,
                 http_client: Optional[Any] = None):
        # http_client: an ``httpx2.Client`` (the SDK's transport); only used by tests.
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2, http_client=http_client)

    def analyze(self, system_prompt: str, user_content: str, json_schema: dict[str, Any]) -> str:
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                thinking={"type": "adaptive"},
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": json_schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise ClaudeUnavailable(f"Claude rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ClaudeUnavailable(f"Claude API error HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeUnavailable(f"Claude connection error: {exc}") from exc

        if response.stop_reason == "refusal":
            raise ClaudeUnavailable("Claude declined to analyze this failure (refusal)")
        if response.stop_reason == "max_tokens":
            raise ClaudeUnavailable("Claude response was truncated (max_tokens)")
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise ClaudeUnavailable("Claude response contained no text block")
        return text

"""Groq LLM client (default analyzer provider).

Uses Groq's JSON mode (``response_format={"type": "json_object"}``) so the
reply is a single JSON object. JSON mode does not enforce our schema, so the
schema is included in the system prompt and the reply is validated with
Pydantic by ``analysis.validate_analysis``. Anything invalid falls back to
the rule-based analyzer, which can never trigger an automatic retry.
"""
from __future__ import annotations

from typing import Any, Optional

import groq

from . import LLMClient, LLMUnavailable


class GroqLLMClient(LLMClient):
    mode = "real"
    provider = "groq"

    def __init__(self, api_key: str, model: str = "openai/gpt-oss-120b", reasoning_effort: str = "medium",
                 timeout: float = 60.0, max_completion_tokens: int = 4096, http_client: Optional[Any] = None):
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_completion_tokens = max_completion_tokens
        self._client = groq.Groq(api_key=api_key, timeout=timeout, max_retries=2, http_client=http_client)

    def analyze(self, system_prompt: str, user_content: str, json_schema: dict[str, Any]) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_completion_tokens": self.max_completion_tokens,
        }
        if self.reasoning_effort:  # only for reasoning models such as openai/gpt-oss-*
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            response = self._client.chat.completions.create(**kwargs)
        except groq.RateLimitError as exc:
            raise LLMUnavailable(f"Groq rate limited: {exc}") from exc
        except groq.APIStatusError as exc:
            raise LLMUnavailable(f"Groq API error HTTP {exc.status_code}") from exc
        except groq.APIConnectionError as exc:
            raise LLMUnavailable(f"Groq connection error: {exc}") from exc

        if not response.choices:
            raise LLMUnavailable("Groq response contained no choices")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMUnavailable("Groq response was truncated (max_completion_tokens)")
        content = choice.message.content
        if not content:
            raise LLMUnavailable("Groq response contained no content")
        return content

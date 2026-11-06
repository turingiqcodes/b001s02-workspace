"""Scriptable LLM mock (stands in for Groq, or Claude). Maps substrings found in the prompt to canned
analysis JSON, so scenarios are deterministic and offline."""
from __future__ import annotations

import json
from typing import Any, Optional

from .. import LLMClient, LLMUnavailable


class MockLLMClient(LLMClient):
    mode = "mock"

    def __init__(self, scripted: Optional[dict[str, Any]] = None, default: Optional[dict[str, Any]] = None,
                 provider: str = "groq") -> None:
        self.provider = provider
        self.available = True
        # substring -> analysis dict, or raw string (to simulate malformed output)
        self.scripted: dict[str, Any] = dict(scripted or {})
        self.default = default or {
            "classification": "uncertain",
            "confidence": 0.4,
            "root_cause": "Mock analysis could not determine the root cause",
            "evidence": ["No scripted response matched the failure"],
            "suggested_fix": "Investigate the task logs manually",
            "proposed_action": "no_action",
            "suspected_commit": None,
        }
        self.prompts: list[str] = []

    def analyze(self, system_prompt: str, user_content: str, json_schema: dict[str, Any]) -> str:
        self.prompts.append(user_content)
        if not self.available:
            raise LLMUnavailable(f"mock {self.provider} is down")
        for needle, response in self.scripted.items():
            if needle in user_content:
                return response if isinstance(response, str) else json.dumps(response)
        return json.dumps(self.default)

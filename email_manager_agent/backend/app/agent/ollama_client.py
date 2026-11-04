"""Ollama chat client (native /api/chat with tool calling)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..errors import ModelError
from .llm import LLMResponse, ToolCall

log = logging.getLogger(__name__)

THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
TOOL_CAPABLE = "qwen3, llama3.1, llama3.2, mistral-nemo"


def strip_thinking(text: str) -> str:
    return THINK_RE.sub("", text or "").strip()


class OllamaClient:
    provider = "ollama"

    def __init__(self, base_url: str, model: str, *, num_ctx: int = 16384, timeout: float = 600.0,
                 keep_alive: str = "30m", think: bool | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.think = think
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), transport=transport)

    # -- conversion --------------------------------------------------------
    @staticmethod
    def to_wire(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]
                                                       if isinstance(c["arguments"], dict) else {}}}
                                         for c in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_name": m["name"], "content": m["content"]})
        return out

    # -- calls -------------------------------------------------------------
    def _error(self, resp: httpx.Response) -> ModelError:
        try:
            detail = str(resp.json().get("error") or "")
        except ValueError:
            detail = resp.text[:300]
        low = detail.lower()
        if resp.status_code == 404 or ("model" in low and "not found" in low):
            return ModelError(f"Ollama model '{self.model}' is not installed. Run: ollama pull {self.model}")
        if "does not support tools" in low:
            return ModelError(f"Model '{self.model}' does not support tool calling. "
                              f"Choose a tool-capable model ({TOOL_CAPABLE}).")
        if "does not support thinking" in low:
            return ModelError(f"Model '{self.model}' does not support thinking. Remove OLLAMA_THINK from .env.")
        return ModelError(f"Ollama returned an error (HTTP {resp.status_code}): {detail or 'no details'}")

    def _connect_error(self) -> ModelError:
        return ModelError(f"Cannot connect to Ollama at {self.base_url}. Start Ollama (`ollama serve`, or open the "
                          "Ollama app) and check OLLAMA_BASE_URL.")

    def chat(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.to_wire(system, messages),
            "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["parameters"]}} for t in tools],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx},
        }
        if self.think is not None:
            payload["think"] = self.think
        try:
            resp = self._client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ModelError(f"The local model did not answer within {self.timeout:g} seconds. Use a smaller model, "
                             "lower OLLAMA_NUM_CTX, set OLLAMA_THINK=false, or raise OLLAMA_TIMEOUT_SECONDS.",
                             status=504) from exc
        except httpx.HTTPError as exc:
            raise self._connect_error() from exc
        if resp.status_code >= 400:
            raise self._error(resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ModelError("Ollama returned a response that isn't JSON.") from exc
        return self.parse(data)

    @staticmethod
    def parse(data: dict[str, Any]) -> LLMResponse:
        message = data.get("message") or {}
        calls = []
        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            args: Any = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    pass  # keep the string; validation reports it to the model
            calls.append(ToolCall(id=call.get("id") or f"call_{i}", name=str(fn.get("name") or ""), arguments=args))
        text = strip_thinking(message.get("content") or "")
        stop = "tool_use" if calls else ("max_tokens" if data.get("done_reason") == "length" else "end")
        return LLMResponse(text=text, tool_calls=calls, stop_reason=stop, raw=None)

    def ping(self) -> tuple[bool | None, str | None]:
        try:
            resp = self._client.get(f"{self.base_url}/api/tags", timeout=10.0)
        except httpx.HTTPError:
            return False, self._connect_error().message
        if resp.status_code >= 400:
            return False, f"Ollama returned HTTP {resp.status_code} for /api/tags."
        try:
            names = {str(m.get("name") or m.get("model")) for m in resp.json().get("models", [])}
        except (ValueError, AttributeError):
            return False, "Ollama returned an unexpected response."
        wanted = {self.model} if ":" in self.model else {self.model, f"{self.model}:latest"}
        if not names & wanted:
            return False, f"Ollama model '{self.model}' is not installed. Run: ollama pull {self.model}"
        return True, None

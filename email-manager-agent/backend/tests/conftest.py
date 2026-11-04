"""Shared fixtures: a scripted fake Claude client and the in-memory mock bridge."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import EmailAgent
from app.bridge import MockBridge
from app.config import Settings


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def tool_use(id_: str, name: str, input_: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def thinking() -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


def response(*blocks: Any, stop_reason: str | None = None) -> SimpleNamespace:
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class FakeClaude:
    """Mimics ``client.beta.messages.create`` by returning queued responses."""

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def script(self, *items: Any) -> None:
        self.queue.extend(items)

    def _create(self, **kwargs: Any) -> Any:
        snapshot = dict(kwargs)
        snapshot["messages"] = copy.deepcopy(kwargs["messages"])
        self.requests.append(snapshot)
        if not self.queue:
            raise AssertionError("FakeClaude ran out of scripted responses")
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def settings() -> Settings:
    return Settings(anthropic_api_key="test", bridge_mode="mock", user_timezone="UTC")


@pytest.fixture
def claude() -> FakeClaude:
    return FakeClaude()


@pytest.fixture
def bridge() -> MockBridge:
    return MockBridge()


@pytest.fixture
def agent(claude: FakeClaude, bridge: MockBridge, settings: Settings) -> EmailAgent:
    return EmailAgent(client=claude, bridge=bridge, settings=settings)

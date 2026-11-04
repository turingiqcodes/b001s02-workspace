from __future__ import annotations

import pytest

from app.config import ConfigError, Settings, is_loopback_host

SECRET = "x" * 20


def full(**kw: str) -> dict[str, str]:
    env = {"OLLAMA_MODEL": "qwen3:8b", "APPS_SCRIPT_URL": "https://script.google.com/macros/s/abc/exec",
           "APPS_SCRIPT_SHARED_SECRET": SECRET}
    env.update(kw)
    return env


def test_defaults_from_empty_env() -> None:
    s = Settings.from_env({})
    assert s.llm_provider == "ollama"
    assert s.ollama_model is None
    assert s.ollama_base_url == "http://127.0.0.1:11434"
    assert s.ollama_num_ctx == 16384
    assert s.ollama_timeout_seconds == 600
    assert s.ollama_keep_alive == "30m"
    assert s.ollama_think is None
    assert s.claude_effort == "medium" and s.claude_refusal_fallback is True
    assert s.work_days == (0, 1, 2, 3, 4)
    assert (s.work_start, s.work_end) == ("09:00", "18:00")
    assert s.max_agent_steps == 8 and s.approval_ttl_seconds == 600
    assert s.backend_host == "127.0.0.1" and s.backend_port == 8000


def test_missing_model_and_bridge_are_issues_not_errors() -> None:
    s = Settings.from_env({})
    assert any("OLLAMA_MODEL" in i for i in s.llm_issues())
    issues = " ".join(s.bridge_issues())
    assert "APPS_SCRIPT_URL" in issues and "APPS_SCRIPT_SHARED_SECRET" in issues


def test_fully_configured_has_no_issues() -> None:
    s = Settings.from_env(full())
    assert s.llm_issues() == [] and s.bridge_issues() == []


def test_claude_provider_needs_key_and_model() -> None:
    s = Settings.from_env(full(LLM_PROVIDER="claude"))
    issues = " ".join(s.llm_issues())
    assert "ANTHROPIC_API_KEY" in issues and "CLAUDE_MODEL" in issues
    ok = Settings.from_env(full(LLM_PROVIDER="Claude", ANTHROPIC_API_KEY="k", CLAUDE_MODEL="claude-opus-5-5"))
    assert ok.llm_issues() == [] and ok.active_model == "claude-opus-5-5"


@pytest.mark.parametrize("url", ["https://example.com/exec", "https://script.google.com/macros/s/abc/dev",
                                 "http://script.google.com/macros/s/abc/exec"])
def test_bad_apps_script_url(url: str) -> None:
    assert any("APPS_SCRIPT_URL" in i for i in Settings.from_env(full(APPS_SCRIPT_URL=url)).bridge_issues())


def test_short_secret_is_an_issue() -> None:
    assert any("16" in i for i in Settings.from_env(full(APPS_SCRIPT_SHARED_SECRET="short")).bridge_issues())


def test_quotes_and_whitespace_are_stripped() -> None:
    s = Settings.from_env(full(APPS_SCRIPT_SHARED_SECRET=f'  "{SECRET}" ', OLLAMA_MODEL="'llama3.2:3b'"))
    assert s.apps_script_shared_secret == SECRET and s.ollama_model == "llama3.2:3b"


def test_work_days_json() -> None:
    assert Settings.from_env({"WORK_DAYS": "[6, 0, 0, 2]"}).work_days == (0, 2, 6)


@pytest.mark.parametrize("value", ["0,1,2", "[]", "[7]", "[true]", '["mon"]'])
def test_work_days_invalid(value: str) -> None:
    with pytest.raises(ConfigError, match="WORK_DAYS"):
        Settings.from_env({"WORK_DAYS": value})


@pytest.mark.parametrize("key,value", [("MAX_AGENT_STEPS", "abc"), ("MAX_AGENT_STEPS", "0"),
                                       ("OLLAMA_NUM_CTX", "10"), ("BACKEND_PORT", "70000"),
                                       ("OLLAMA_TIMEOUT_SECONDS", "fast"), ("OLLAMA_THINK", "maybe"),
                                       ("CLAUDE_EFFORT", "extreme"), ("LLM_PROVIDER", "openai"),
                                       ("WORK_START", "9am"), ("LOG_LEVEL", "LOUD")])
def test_invalid_values_raise(key: str, value: str) -> None:
    with pytest.raises(ConfigError, match=key):
        Settings.from_env({key: value})


def test_work_end_must_follow_start() -> None:
    with pytest.raises(ConfigError, match="WORK_END"):
        Settings.from_env({"WORK_START": "18:00", "WORK_END": "09:00"})


def test_bad_timezone_falls_back_to_utc_with_issue() -> None:
    s = Settings.from_env({"USER_TIMEZONE": "Mars/Base"})
    assert s.user_timezone == "UTC" and "Mars/Base" in (s.timezone_issue or "")


def test_think_flag_parses() -> None:
    assert Settings.from_env({"OLLAMA_THINK": "false"}).ollama_think is False
    assert Settings.from_env({"OLLAMA_THINK": "true"}).ollama_think is True


def test_cors_origins_include_both_local_forms() -> None:
    origins = Settings.from_env({}).cors_origins
    assert "http://localhost:5173" in origins and "http://127.0.0.1:5173" in origins


def test_limits_payload() -> None:
    limits = Settings.from_env({"MAX_ATTACHMENT_MB": "5"}).limits()
    assert limits["max_attachment_mb"] == 5 and limits["work_days"] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("host,ok", [("127.0.0.1", True), ("localhost", True), ("::1", True), ("127.0.0.2", True),
                                     ("0.0.0.0", False), ("192.168.1.5", False), ("example.com", False)])
def test_is_loopback_host(host: str, ok: bool) -> None:
    assert is_loopback_host(host) is ok

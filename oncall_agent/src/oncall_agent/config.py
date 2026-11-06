"""Configuration: environment variables + JSON config files, validated at startup.

Everything is loaded into Pydantic models so a typo in a config file fails fast
with a clear message instead of causing odd behaviour hours later.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


def _bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 0) -> int:
    raw = env.get(key, "")
    if raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {value}")
    return value


class Settings(BaseModel):
    """Values that come from environment variables (see .env.example)."""

    poll_interval_seconds: int = 60
    max_auto_retries: int = 2
    max_retries_per_hour: int = 10
    retry_timeout_minutes: int = 30
    detection_overlap_minutes: int = 10
    initial_lookback_hours: int = 24
    auto_retry_confidence_threshold: float = 0.7
    force_mocks: bool = False
    # Only DAGs listed in dag_repos.json are monitored unless this is true (important on a shared Airflow).
    monitor_all_dags: bool = False

    llm_provider: str = "groq"  # groq (default) | claude
    groq_api_key: str = Field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    groq_reasoning_effort: str = "medium"  # low | medium | high for reasoning models; "" = don't send
    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_model: str = "claude-opus-5-5"

    airflow_base_url: str = "http://localhost:18080"
    airflow_username: str = ""
    airflow_password: str = Field(default="", repr=False)

    github_token: str = Field(default="", repr=False)

    linear_api_key: str = Field(default="", repr=False)
    linear_team_id: str = ""

    mcp_allow_airflow_writes: bool = False

    database_path: str = "./data/oncall_agent.db"
    dag_repos_path: str = "./config/dag_repos.json"
    owners_path: str = "./config/owners.json"

    # ---- which integrations are real? -------------------------------------------------
    @property
    def airflow_is_real(self) -> bool:
        return not self.force_mocks and bool(self.airflow_username and self.airflow_password)

    @property
    def github_is_real(self) -> bool:
        return not self.force_mocks and bool(self.github_token)

    @property
    def linear_is_real(self) -> bool:
        return not self.force_mocks and bool(self.linear_api_key and self.linear_team_id)

    @property
    def llm_is_real(self) -> bool:
        if self.force_mocks:
            return False
        if self.llm_provider == "claude":
            return bool(self.anthropic_api_key)
        return bool(self.groq_api_key)

    @property
    def llm_model(self) -> str:
        return self.anthropic_model if self.llm_provider == "claude" else self.groq_model

    def secrets(self) -> list[str]:
        """All secret values, used by the audit redactor."""
        return [s for s in (self.groq_api_key, self.anthropic_api_key, self.airflow_password, self.github_token,
                            self.linear_api_key) if s]


def load_settings(env: Optional[Mapping[str, str]] = None, *, load_dotenv_file: bool = True) -> Settings:
    """Build Settings from the environment (and a .env file when present)."""
    if env is None:
        if load_dotenv_file:
            try:
                from dotenv import load_dotenv

                load_dotenv(override=False)
            except ImportError:  # python-dotenv is optional at runtime
                pass
        env = os.environ
    provider = (env.get("ONCALL_LLM_PROVIDER", "") or "groq").strip().lower()
    if provider not in {"groq", "claude"}:
        raise ConfigError(f"ONCALL_LLM_PROVIDER must be 'groq' or 'claude', got {provider!r}")
    effort = env.get("GROQ_REASONING_EFFORT", "medium").strip().lower()
    if effort not in {"", "low", "medium", "high"}:
        raise ConfigError("GROQ_REASONING_EFFORT must be low, medium, high, or empty")
    return Settings(
        llm_provider=provider,
        groq_api_key=env.get("GROQ_API_KEY", "").strip(),
        groq_model=env.get("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b",
        groq_reasoning_effort=effort,
        poll_interval_seconds=_int(env, "ONCALL_POLL_INTERVAL_SECONDS", 60, minimum=1),
        max_auto_retries=_int(env, "ONCALL_MAX_AUTO_RETRIES", 2),
        max_retries_per_hour=_int(env, "ONCALL_MAX_RETRIES_PER_HOUR", 10),
        retry_timeout_minutes=_int(env, "ONCALL_RETRY_TIMEOUT_MINUTES", 30, minimum=1),
        detection_overlap_minutes=_int(env, "ONCALL_DETECTION_OVERLAP_MINUTES", 10),
        initial_lookback_hours=_int(env, "ONCALL_INITIAL_LOOKBACK_HOURS", 24, minimum=1),
        force_mocks=_bool(env.get("ONCALL_FORCE_MOCKS")),
        monitor_all_dags=_bool(env.get("ONCALL_MONITOR_ALL_DAGS")),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=env.get("ANTHROPIC_MODEL", "") or "claude-opus-5-5",
        airflow_base_url=(env.get("AIRFLOW_BASE_URL", "") or "http://localhost:18080").rstrip("/"),
        airflow_username=env.get("AIRFLOW_USERNAME", ""),
        airflow_password=env.get("AIRFLOW_PASSWORD", ""),
        github_token=env.get("GITHUB_TOKEN", ""),
        linear_api_key=env.get("LINEAR_API_KEY", ""),
        linear_team_id=env.get("LINEAR_TEAM_ID", ""),
        mcp_allow_airflow_writes=_bool(env.get("ONCALL_MCP_ALLOW_AIRFLOW_WRITES")),
        database_path=env.get("ONCALL_DATABASE_PATH", "") or "./data/oncall_agent.db",
        dag_repos_path=env.get("ONCALL_DAG_REPOS_PATH", "") or "./config/dag_repos.json",
        owners_path=env.get("ONCALL_OWNERS_PATH", "") or "./config/owners.json",
    )


# ---------------------------------------------------------------------------------------
# dag_repos.json
# ---------------------------------------------------------------------------------------
class DagRepoEntry(BaseModel):
    repo: str
    dag_file_path: str
    extra_paths: list[str] = Field(default_factory=list)
    owner: Optional[str] = None  # key into owners.json "developers"
    no_retry: bool = False

    @field_validator("repo")
    @classmethod
    def _repo_format(cls, v: str) -> str:
        if v.count("/") != 1 or not all(v.split("/")):
            raise ValueError(f"repo must look like 'owner/name', got {v!r}")
        return v


class DagReposConfig(BaseModel):
    dags: dict[str, DagRepoEntry] = Field(default_factory=dict)
    no_retry_dags: list[str] = Field(default_factory=list)

    def entry(self, dag_id: str) -> Optional[DagRepoEntry]:
        return self.dags.get(dag_id)

    def is_no_retry(self, dag_id: str) -> bool:
        entry = self.dags.get(dag_id)
        return dag_id in self.no_retry_dags or bool(entry and entry.no_retry)

    def dag_id_for_file(self, filename: str) -> Optional[str]:
        """Map an import-error filename back to a configured DAG id (best effort)."""
        norm = filename.replace("\\", "/")
        for dag_id, entry in self.dags.items():
            path = entry.dag_file_path.lstrip("./")
            if norm.endswith(path) or Path(norm).name == Path(path).name:
                return dag_id
        return None


# ---------------------------------------------------------------------------------------
# owners.json
# ---------------------------------------------------------------------------------------
class Developer(BaseModel):
    github_username: str
    linear_user_id: str


class OwnersConfig(BaseModel):
    developers: dict[str, Developer] = Field(default_factory=dict)
    default_owner: str
    approvers: list[str] = Field(default_factory=list)  # Linear user ids allowed to approve

    @field_validator("default_owner")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("default_owner must be a non-empty Linear user id")
        return v

    @model_validator(mode="after")
    def _unique_github(self) -> "OwnersConfig":
        seen: dict[str, str] = {}
        for key, dev in self.developers.items():
            login = dev.github_username.lower()
            if login in seen:
                raise ValueError(f"github_username {dev.github_username!r} used by both {seen[login]!r} and {key!r}")
            seen[login] = key
        return self

    def known_linear_ids(self) -> set[str]:
        return {d.linear_user_id for d in self.developers.values()} | {self.default_owner}


def _read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc


def load_dag_repos(path: str) -> DagReposConfig:
    try:
        return DagReposConfig.model_validate(_read_json(path))
    except ValidationError as exc:
        raise ConfigError(f"{path} is invalid:\n{exc}") from exc


def load_owners(path: str) -> OwnersConfig:
    try:
        return OwnersConfig.model_validate(_read_json(path))
    except ValidationError as exc:
        raise ConfigError(f"{path} is invalid:\n{exc}") from exc


def validate_cross_references(dag_repos: DagReposConfig, owners: OwnersConfig) -> list[str]:
    """Return a list of problems (DAG owners that don't exist in owners.json)."""
    problems = []
    for dag_id, entry in dag_repos.dags.items():
        if entry.owner and entry.owner not in owners.developers:
            problems.append(f"dag {dag_id!r} owner {entry.owner!r} is not listed in owners.json developers")
    return problems

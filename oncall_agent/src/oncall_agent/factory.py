"""Builds every component from configuration. The agent, the CLI, the MCP
servers and the simulation all use this, so they share the same clients."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .agent import OncallAgent
from .analysis import Analyzer
from .approval_manager import ApprovalManager
from .audit import AuditLogger
from .config import (
    ConfigError,
    DagReposConfig,
    OwnersConfig,
    Settings,
    load_dag_repos,
    load_owners,
    validate_cross_references,
)
from .context import ContextCollector
from .detection import Detector
from .incident_manager import IncidentManager
from .integrations import AirflowClient, GitHubClient, LinearClient, LLMClient
from .policy import DecisionPolicy
from .retry_manager import RetryManager
from .store import Store


@dataclass
class Services:
    settings: Settings
    dag_repos: DagReposConfig
    owners: OwnersConfig
    store: Store
    audit: AuditLogger
    airflow: AirflowClient
    github: GitHubClient
    linear: LinearClient
    llm: Optional[LLMClient]
    analyzer: Analyzer
    policy: DecisionPolicy
    collector: ContextCollector
    detector: Detector
    incidents: IncidentManager
    retries: RetryManager
    approvals: ApprovalManager
    agent: OncallAgent

    def modes(self) -> dict[str, str]:
        return {
            "airflow": self.airflow.mode,
            "github": self.github.mode,
            "linear": self.linear.mode,
            "llm": f"{self.llm.provider} ({self.llm.mode})" if self.llm else "unavailable (rule-based analyzer)",
        }


def _all_mock(settings: Settings) -> bool:
    return not (settings.airflow_is_real or settings.github_is_real or settings.linear_is_real)


def load_configs(settings: Settings) -> tuple[DagReposConfig, OwnersConfig, list[str]]:
    """Load + validate config files. In fully-mocked local mode, fall back to the
    *.example.json files so the project runs out of the box."""
    notes: list[str] = []
    paths = {"dag_repos": settings.dag_repos_path, "owners": settings.owners_path}
    for key, path in list(paths.items()):
        if not Path(path).exists():
            example = str(Path(path).with_suffix("")) + ".example.json"
            if _all_mock(settings) and Path(example).exists():
                notes.append(f"{path} not found; using {example} (mock mode only)")
                paths[key] = example
    dag_repos = load_dag_repos(paths["dag_repos"])
    owners = load_owners(paths["owners"])
    problems = validate_cross_references(dag_repos, owners)
    if problems:
        raise ConfigError("; ".join(problems))
    if not owners.approvers:
        notes.append("owners.json has no approvers: risky actions can never be approved")
    return dag_repos, owners, notes


def build_clients(settings: Settings) -> tuple[AirflowClient, GitHubClient, LinearClient, Optional[LLMClient]]:
    from .integrations.mocks import MockAirflowClient, MockGitHubClient, MockLinearClient

    if settings.airflow_is_real:
        from .integrations.airflow import RestAirflowClient

        airflow: AirflowClient = RestAirflowClient(settings.airflow_base_url, settings.airflow_username,
                                                   settings.airflow_password)
    else:
        airflow = MockAirflowClient()
    if settings.github_is_real:
        from .integrations.github import RestGitHubClient

        github: GitHubClient = RestGitHubClient(settings.github_token)
    else:
        github = MockGitHubClient()
    if settings.linear_is_real:
        from .integrations.linear import GraphQLLinearClient

        linear: LinearClient = GraphQLLinearClient(settings.linear_api_key, settings.linear_team_id)
    else:
        linear = MockLinearClient()
    llm: Optional[LLMClient] = None
    if settings.llm_is_real:
        if settings.llm_provider == "claude":
            from .integrations.claude import AnthropicClaudeClient

            llm = AnthropicClaudeClient(settings.anthropic_api_key, settings.anthropic_model)
        else:
            from .integrations.groq import GroqLLMClient

            llm = GroqLLMClient(settings.groq_api_key, settings.groq_model, settings.groq_reasoning_effort)
    return airflow, github, linear, llm


def build_services(
    settings: Settings,
    *,
    dag_repos: Optional[DagReposConfig] = None,
    owners: Optional[OwnersConfig] = None,
    store: Optional[Store] = None,
    airflow: Optional[AirflowClient] = None,
    github: Optional[GitHubClient] = None,
    linear: Optional[LinearClient] = None,
    llm: Optional[LLMClient] = None,
    use_llm_client: bool = True,
) -> Services:
    """Wire everything together. Any component can be injected (tests, simulation)."""
    if dag_repos is None or owners is None:
        loaded_repos, loaded_owners, _ = load_configs(settings)
        dag_repos = dag_repos or loaded_repos
        owners = owners or loaded_owners
    if airflow is None or github is None or linear is None or (llm is None and use_llm_client):
        a, g, l, m = build_clients(settings)
        airflow, github, linear = airflow or a, github or g, linear or l
        if llm is None and use_llm_client:
            llm = m
    store = store or Store(settings.database_path)
    audit = AuditLogger(store, settings.secrets())
    analyzer = Analyzer(llm)
    policy = DecisionPolicy(settings, dag_repos)
    collector = ContextCollector(airflow, github, dag_repos)
    detector = Detector(airflow, store, dag_repos, settings)
    incidents = IncidentManager(linear, store, audit, owners)
    retries = RetryManager(airflow, store, audit, settings, policy, incidents)
    approvals = ApprovalManager(airflow, linear, store, audit, owners, incidents, settings)
    agent = OncallAgent(settings=settings, store=store, audit=audit, airflow=airflow, detector=detector,
                        collector=collector, analyzer=analyzer, policy=policy, retries=retries,
                        approvals=approvals, incidents=incidents, owners=owners, dag_repos=dag_repos)
    return Services(settings=settings, dag_repos=dag_repos, owners=owners, store=store, audit=audit,
                    airflow=airflow, github=github, linear=linear, llm=llm, analyzer=analyzer,
                    policy=policy, collector=collector, detector=detector, incidents=incidents,
                    retries=retries, approvals=approvals, agent=agent)

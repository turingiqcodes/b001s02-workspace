"""Who should own an unresolved incident?

Priority: suspected commit author -> DAG owner -> default owner.
Only identities present in owners.json are ever used; nothing is invented.
"""
from __future__ import annotations

from typing import Optional

from .config import DagReposConfig, OwnersConfig
from .models import CommitInfo, OwnerAssignment


def resolve_owner(dag_id: str, suspected_commit: Optional[CommitInfo], owners: OwnersConfig,
                  dag_repos: DagReposConfig) -> OwnerAssignment:
    notes: list[str] = []

    if suspected_commit is not None:
        login = (suspected_commit.author_login or "").lower()
        for key, dev in owners.developers.items():
            if login and dev.github_username.lower() == login:
                return OwnerAssignment(
                    linear_user_id=dev.linear_user_id, developer=key, source="commit_author",
                    reason=f"Author of suspected commit {suspected_commit.sha[:10]} "
                           f"(GitHub @{suspected_commit.author_login}) is developer '{key}'.")
        who = f"@{suspected_commit.author_login}" if suspected_commit.author_login else "an unknown GitHub user"
        notes.append(f"Suspected commit {suspected_commit.sha[:10]} was authored by {who}, "
                     f"who is not mapped in owners.json.")
    else:
        notes.append("No suspected commit was identified.")

    entry = dag_repos.entry(dag_id)
    if entry and entry.owner:
        dev = owners.developers.get(entry.owner)
        if dev:
            return OwnerAssignment(
                linear_user_id=dev.linear_user_id, developer=entry.owner, source="dag_owner",
                reason=" ".join(notes + [f"Falling back to the DAG owner '{entry.owner}' from dag_repos.json."]))
        notes.append(f"DAG owner '{entry.owner}' is not listed in owners.json.")
    else:
        notes.append(f"DAG '{dag_id}' has no owner configured.")

    return OwnerAssignment(
        linear_user_id=owners.default_owner, developer=None, source="default_owner",
        reason=" ".join(notes + ["Falling back to the configured default owner."]))

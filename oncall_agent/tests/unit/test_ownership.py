from datetime import datetime, timezone

from oncall_agent.models import CommitInfo
from oncall_agent.ownership import resolve_owner
from oncall_agent.simulate import sim_configs

REPOS, OWNERS = sim_configs()


def commit(login):
    return CommitInfo(sha="c0ffee" * 6 + "abcd", author_login=login, author_name=None, message="m",
                      timestamp=datetime.now(timezone.utc))


def test_commit_author_has_priority():
    a = resolve_owner("orders_dag", commit("Alice-Dev"), OWNERS, REPOS)  # case-insensitive
    assert (a.linear_user_id, a.source) == ("lin-alice", "commit_author")


def test_unmapped_author_falls_back_to_dag_owner():
    a = resolve_owner("orders_dag", commit("stranger"), OWNERS, REPOS)
    assert (a.linear_user_id, a.source) == ("lin-bob", "dag_owner")
    assert "stranger" in a.reason and "not mapped" in a.reason


def test_no_commit_uses_dag_owner_then_default():
    assert resolve_owner("orders_dag", None, OWNERS, REPOS).source == "dag_owner"
    a = resolve_owner("unknown_dag", None, OWNERS, REPOS)
    assert (a.linear_user_id, a.source) == ("lin-oncall-default", "default_owner")


def test_never_invents_identities():
    known = OWNERS.known_linear_ids()
    for dag in ("orders_dag", "unknown_dag"):
        for c in (None, commit("nobody")):
            assert resolve_owner(dag, c, OWNERS, REPOS).linear_user_id in known

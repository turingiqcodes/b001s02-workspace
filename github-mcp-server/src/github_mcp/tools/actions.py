"""CI/CD tools backed by GitHub Actions."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context

from github_mcp.github_client import gh
from github_mcp.tools import Registrar, slim_run

MAX_LOG_CHARS = 20_000


def register(r: Registrar) -> None:
    @r.read("List workflows")
    async def list_workflows(owner: str, repo: str, ctx: Context) -> list[dict[str, Any]]:
        """List the GitHub Actions workflows defined in a repository."""
        data = await gh(ctx).get(f"/repos/{owner}/{repo}/actions/workflows", params={"per_page": 100})
        return [{"id": w["id"], "name": w["name"], "path": w["path"], "state": w["state"]} for w in data["workflows"]]

    @r.read("List workflow runs")
    async def list_workflow_runs(
        owner: str,
        repo: str,
        ctx: Context,
        workflow: str | None = None,
        branch: str | None = None,
        status: str | None = None,
        event: str | None = None,
        per_page: int = 20,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List workflow runs. workflow is a workflow id or file name (e.g. ci.yml).
        status accepts completed, in_progress, queued, success, failure, etc."""
        path = f"/repos/{owner}/{repo}/actions/workflows/{workflow}/runs" if workflow else f"/repos/{owner}/{repo}/actions/runs"
        params = {"branch": branch, "status": status, "event": event, "per_page": per_page, "page": page}
        data = await gh(ctx).get(path, params=params)
        return [slim_run(x) for x in data["workflow_runs"]]

    @r.read("Get workflow run")
    async def get_workflow_run(owner: str, repo: str, run_id: int, ctx: Context) -> dict[str, Any]:
        """Get the status and conclusion of one workflow run."""
        run = await gh(ctx).get(f"/repos/{owner}/{repo}/actions/runs/{run_id}")
        return {**slim_run(run), "run_attempt": run.get("run_attempt"), "updated_at": run.get("updated_at")}

    @r.read("List workflow jobs")
    async def list_workflow_jobs(owner: str, repo: str, run_id: int, ctx: Context) -> list[dict[str, Any]]:
        """List the jobs of a workflow run, with each job's failed steps."""
        data = await gh(ctx).get(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", params={"per_page": 100})
        return [
            {
                "id": j["id"],
                "name": j["name"],
                "status": j["status"],
                "conclusion": j.get("conclusion"),
                "failed_steps": [s["name"] for s in j.get("steps", []) if s.get("conclusion") == "failure"],
                "html_url": j.get("html_url"),
            }
            for j in data["jobs"]
        ]

    @r.read("Get job logs")
    async def get_job_logs(owner: str, repo: str, job_id: int, ctx: Context, tail_chars: int = MAX_LOG_CHARS) -> str:
        """Get the plain-text log of a job. Returns the last tail_chars characters, where failures usually are."""
        log = await gh(ctx).get(f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs", follow_redirects=True)
        log = log if isinstance(log, str) else str(log)
        if len(log) > tail_chars:
            return f"[showing last {tail_chars} of {len(log)} characters]\n" + log[-tail_chars:]
        return log

    @r.write("Trigger workflow")
    async def trigger_workflow(
        owner: str,
        repo: str,
        workflow: str,
        ref: str,
        ctx: Context,
        inputs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a workflow that has a workflow_dispatch trigger. workflow is an id or file name."""
        await gh(ctx).post(
            f"/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches", json={"ref": ref, "inputs": inputs or {}}
        )
        return {"dispatched": True, "workflow": workflow, "ref": ref}

    @r.write("Rerun workflow run")
    async def rerun_workflow_run(owner: str, repo: str, run_id: int, ctx: Context, failed_only: bool = True) -> dict[str, Any]:
        """Re-run a workflow run; by default only its failed jobs."""
        suffix = "rerun-failed-jobs" if failed_only else "rerun"
        await gh(ctx).post(f"/repos/{owner}/{repo}/actions/runs/{run_id}/{suffix}")
        return {"rerun_requested": True, "run_id": run_id, "failed_only": failed_only}

    @r.write("Cancel workflow run", destructive=True)
    async def cancel_workflow_run(owner: str, repo: str, run_id: int, ctx: Context) -> dict[str, Any]:
        """Cancel an in-progress workflow run."""
        await gh(ctx).post(f"/repos/{owner}/{repo}/actions/runs/{run_id}/cancel")
        return {"cancel_requested": True, "run_id": run_id}

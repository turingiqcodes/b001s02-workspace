# GitHub MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an AI client (Claude Desktop, Claude Code, an IDE, or your own agent) work with GitHub: repositories, pull requests, CI/CD (GitHub Actions), issues and search. It runs over **local stdio** and over **remote Streamable HTTP**.

```
┌────────────┐   JSON-RPC over          ┌───────────────────┐   HTTPS REST    ┌──────────────┐
│ MCP client │ ── stdio (local)  ─────▶ │ github-mcp-server │ ──────────────▶ │  GitHub API  │
│ (Claude,   │ ── HTTP /mcp (remote) ─▶ │  38 tools in 5    │ ◀────────────── │ api.github…  │
│  IDE, app) │ ◀─ results ───────────── │  toolsets         │                 └──────────────┘
└────────────┘                          └───────────────────┘
```

1. The client discovers the tools (`tools/list`) and the model picks one, e.g. `list_workflow_runs`.
2. The client sends `tools/call` over stdio or HTTP.
3. The server picks a GitHub token, calls the REST API, cuts the response down to the useful fields and returns structured JSON.
4. When GitHub returns an error (404, 401, rate limit), the client gets a tool result with `isError: true` and a readable message, so the model can correct itself.

## Tools

| Toolset (`--toolsets`) | Read tools | Write tools |
|---|---|---|
| `repos` | get_repository, list_repositories, list_branches, get_file_contents, list_commits, get_commit | create_repository, create_branch, create_or_update_file |
| `pulls` | list_pull_requests, get_pull_request, get_pull_request_files, get_pull_request_diff, list_pull_request_reviews | create_pull_request, update_pull_request, request_reviewers, create_pull_request_review, merge_pull_request |
| `actions` (CI/CD) | list_workflows, list_workflow_runs, get_workflow_run, list_workflow_jobs, get_job_logs | trigger_workflow, rerun_workflow_run, cancel_workflow_run |
| `issues` | list_issues, get_issue, list_issue_comments | create_issue, update_issue, add_issue_comment |
| `search` | search_repositories, search_code, search_issues, search_commits, search_users | — |

Every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`; merge and cancel are marked destructive), so clients can ask for confirmation before writes. `--read-only` registers only the 24 read tools.

## Setup

```bash
cd github-mcp-server
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...   # scopes: repo, workflow (+ read:org for org repos)
```

## Transport 1: local stdio

The client starts the server as a subprocess, and JSON-RPC goes over its stdin/stdout. Logs go to stderr.

**Claude Code**
```bash
claude mcp add github --env GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... -- /abs/path/github-mcp-server/.venv/bin/github-mcp-server
```

**Claude Desktop** (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "github": {
      "command": "/abs/path/github-mcp-server/.venv/bin/github-mcp-server",
      "args": ["--toolsets", "repos,pulls,actions,issues,search"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..." }
    }
  }
}
```

## Transport 2: remote Streamable HTTP

```bash
github-mcp-server --transport http --port 8000            # http://127.0.0.1:8000/mcp
curl localhost:8000/health
```

**Authentication.** Each HTTP client sends its own GitHub token as `Authorization: Bearer <pat>`. The server forwards that token to GitHub, so one server can serve several users, and each user acts with their own permissions. If a request has no header, the server falls back to its own `GITHUB_PERSONAL_ACCESS_TOKEN`. **Leave that variable unset on any shared or public server**, or anyone who can reach the server can use that token.

**Claude Code**
```bash
claude mcp add --transport http github http://127.0.0.1:8000/mcp --header "Authorization: Bearer ghp_..."
```

**Deploying beyond localhost.** On 127.0.0.1, protection against DNS rebinding turns on automatically. When you bind to a public interface, list the host names clients will use:
```bash
github-mcp-server --transport http --host 0.0.0.0 --allowed-hosts "mcp.example.com" --stateless
docker build -t github-mcp . && docker run -p 8000:8000 -e ALLOWED_HOSTS="mcp.example.com" github-mcp
```
Put TLS in front of it with a reverse proxy, because tokens travel in the header. Use `--stateless` when several instances run behind a load balancer.

## Demo client

`client/demo_client.py` connects over either transport:

```bash
python client/demo_client.py stdio list
python client/demo_client.py stdio call get_repository '{"owner":"octocat","repo":"Hello-World"}'

github-mcp-server --transport http &        # in another terminal
python client/demo_client.py http call list_workflow_runs '{"owner":"OWNER","repo":"REPO","status":"failure"}'
```

## Configuration

| Env var / flag | Default | Purpose |
|---|---|---|
| `GITHUB_PERSONAL_ACCESS_TOKEN` (or `GITHUB_TOKEN`) | — | Token for stdio, and the fallback token for HTTP |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub Enterprise: `https://HOST/api/v3` |
| `GITHUB_TOOLSETS` / `--toolsets` | all | e.g. `repos,issues` |
| `GITHUB_READ_ONLY` / `--read-only` | false | Register only the read tools |
| `--transport` | `stdio` | `stdio` or `http` |
| `--host` / `--port` / `--path` | `127.0.0.1` / `8000` / `/mcp` | HTTP endpoint |
| `--allowed-hosts` | localhost | Host header values allowed for HTTP |
| `--stateless` | off | Stateless HTTP sessions |

## Layout

```
src/github_mcp/
  server.py          entrypoint, transport selection, /health
  config.py          env settings
  github_client.py   REST wrapper: auth, error mapping, per-request token
  tools/__init__.py  registrar (read-only gating, annotations), response trimming
  tools/{repos,pulls,actions,issues,search}.py
client/demo_client.py
tests/test_tools.py  in-process MCP client + mocked GitHub (respx)
```

## Tests

```bash
pytest -q
```

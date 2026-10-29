"""GitHub MCP server entrypoint.

Flow:  MCP client  --(stdio | Streamable HTTP)-->  this server  --(HTTPS REST)-->  GitHub API

    github-mcp-server                         # stdio (local; launched by the MCP client)
    github-mcp-server --transport http        # Streamable HTTP at http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import argparse
import logging
import sys
from importlib import import_module

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from github_mcp.config import ALL_TOOLSETS, settings
from github_mcp.tools import Registrar

INSTRUCTIONS = """Tools for working with GitHub: repositories, pull requests, GitHub Actions CI/CD,
issues and search. Identify repositories with owner + repo (e.g. owner='octocat', repo='hello-world').
Prefer the search_* tools to find things across repositories; use list_* tools inside a known repo.
For a failing CI run: list_workflow_runs -> list_workflow_jobs -> get_job_logs."""


def build_server(toolsets: tuple[str, ...] | None = None, read_only: bool | None = None) -> MCPServer:
    toolsets = toolsets if toolsets is not None else settings.toolsets
    read_only = settings.read_only if read_only is None else read_only
    unknown = set(toolsets) - set(ALL_TOOLSETS)
    if unknown:
        raise ValueError(f"Unknown toolsets {sorted(unknown)}; choose from {', '.join(ALL_TOOLSETS)}")

    mcp = MCPServer(name="github-mcp-server", version="0.1.0", instructions=INSTRUCTIONS)
    registrar = Registrar(mcp, read_only=read_only)
    for name in toolsets:
        import_module(f"github_mcp.tools.{name}").register(registrar)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "toolsets": list(toolsets), "read_only": read_only, "tools": len(registrar.registered)})

    logging.getLogger(__name__).info("Registered %d tools from toolsets %s", len(registrar.registered), ",".join(toolsets))
    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="GitHub MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address (http transport)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (http transport)")
    parser.add_argument("--path", default="/mcp", help="HTTP endpoint path (http transport)")
    parser.add_argument("--toolsets", help=f"Comma-separated subset of {','.join(ALL_TOOLSETS)}")
    parser.add_argument("--read-only", action="store_true", help="Register only read-only tools")
    parser.add_argument(
        "--allowed-hosts",
        help="Comma-separated Host header values allowed when binding beyond localhost, e.g. 'mcp.example.com,10.0.0.5:*'",
    )
    parser.add_argument("--stateless", action="store_true", help="Stateless HTTP (for load-balanced deployments)")
    args = parser.parse_args(argv)

    # stdout is the protocol channel in stdio mode, so all logs go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    toolsets = tuple(t.strip() for t in args.toolsets.split(",")) if args.toolsets else None
    mcp = build_server(toolsets, read_only=args.read_only or None)

    if args.transport == "stdio":
        if not settings.token:
            logging.warning("GITHUB_PERSONAL_ACCESS_TOKEN is not set; tool calls will fail")
        anyio.run(mcp.run_stdio_async)
        return

    security = None
    if args.allowed_hosts:
        hosts = [h.strip() for h in args.allowed_hosts.split(",") if h.strip()]
        security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=[f"https://{h}" for h in hosts])
    logging.info("Streamable HTTP on http://%s:%d%s", args.host, args.port, args.path)

    async def serve() -> None:
        await mcp.run_streamable_http_async(
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
            stateless_http=args.stateless,
            transport_security=security,
        )

    anyio.run(serve)


if __name__ == "__main__":
    main()

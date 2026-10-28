"""Example MCP client for the GitHub MCP server, over either transport.

    # Local: the client launches the server as a subprocess and speaks over stdin/stdout
    python client/demo_client.py stdio list
    python client/demo_client.py stdio call get_repository '{"owner": "octocat", "repo": "Hello-World"}'

    # Remote: the server is already running (github-mcp-server --transport http)
    python client/demo_client.py http list --url http://127.0.0.1:8000/mcp
    python client/demo_client.py http call search_repositories '{"query": "mcp language:python"}'

Over HTTP the client sends GITHUB_PERSONAL_ACCESS_TOKEN as a Bearer token, so the
server acts on GitHub with the caller's identity.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import asynccontextmanager

import anyio
import httpx2
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def connect(transport: str, url: str):
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")
    if transport == "stdio":
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "github_mcp"],
            env={**os.environ, **({"GITHUB_PERSONAL_ACCESS_TOKEN": token} if token else {})},
        )
        async with Client(params) as client:
            yield client
    else:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx2.AsyncClient(headers=headers, timeout=60) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                yield client


async def run(args: argparse.Namespace) -> int:
    async with connect(args.transport, args.url) as client:
        info = client.server_info
        print(f"connected to {info.name if info else '?'} via {args.transport}", file=sys.stderr)
        if args.command == "list":
            tools = await client.list_tools()
            for t in tools.tools:
                ro = "read " if t.annotations and t.annotations.read_only_hint else "write"
                print(f"[{ro}] {t.name:32} {(t.description or '').splitlines()[0]}")
            return 0
        result = await client.call_tool(args.tool, json.loads(args.arguments))
        if result.is_error:
            print("ERROR:", *(c.text for c in result.content if c.type == "text"), file=sys.stderr)
            return 1
        if result.structured_content is not None:
            print(json.dumps(result.structured_content, indent=2))
        else:
            print("\n".join(c.text for c in result.content if c.type == "text"))
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub MCP demo client")
    parser.add_argument("transport", choices=["stdio", "http"])
    parser.add_argument("command", choices=["list", "call"])
    parser.add_argument("tool", nargs="?")
    parser.add_argument("arguments", nargs="?", default="{}")
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    args = parser.parse_args()
    if args.command == "call" and not args.tool:
        parser.error("call needs a tool name")
    sys.exit(anyio.run(run, args))


if __name__ == "__main__":
    main()

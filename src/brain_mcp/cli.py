"""Entrypoint: one command, both transports."""

from __future__ import annotations

import argparse
import os
import sys

from brain_mcp.config import ConfigError, load_config
from brain_mcp.server import build_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="brain-mcp",
        description="MCP server exposing a git-backed markdown brain",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("BRAIN_MCP_TRANSPORT", "stdio"),
        help="stdio for local agents (default), http for Streamable HTTP",
    )
    parser.add_argument("--host", default=None, help="override BRAIN_MCP_HOST")
    parser.add_argument("--port", type=int, default=None, help="override BRAIN_MCP_PORT")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"brain-mcp: {exc}", file=sys.stderr)
        sys.exit(2)

    server = build_server(config)
    if args.transport == "stdio":
        server.run()
    else:
        server.run(
            transport="http",
            host=args.host or config.host,
            port=args.port or config.port,
        )


if __name__ == "__main__":
    main()

"""Entrypoint for the OAuth gateway."""

from __future__ import annotations

import argparse
import sys

import uvicorn

from oauth_gateway.app import build_app
from oauth_gateway.config import ConfigError, load_config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="oauth-gateway",
        description="OAuth 2.1 + DCR gateway bridging Claude.ai's connector to brain-mcp",
    )
    parser.add_argument("--host", default=None, help="override GATEWAY_HOST")
    parser.add_argument("--port", type=int, default=None, help="override GATEWAY_PORT")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"oauth-gateway: {exc}", file=sys.stderr)
        sys.exit(2)

    app = build_app(config)
    uvicorn.run(
        app,
        host=args.host or config.host,
        port=args.port or config.port,
        server_header=False,
    )


if __name__ == "__main__":
    main()

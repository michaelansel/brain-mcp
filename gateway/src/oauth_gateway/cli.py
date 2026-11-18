"""Entrypoint for the OAuth gateway."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import time

import uvicorn

from oauth_gateway import db
from oauth_gateway.app import build_app
from oauth_gateway.config import ConfigError, load_config

# The name ends up in the exe.dev key label (and so in a shell command), so keep it plain.
_CLIENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def add_client(argv: list[str]) -> None:
    """Register a confidential client (one that can't do DCR, e.g. Home Assistant) and
    print its client_id and secret once. Only the secret's hash is stored."""
    parser = argparse.ArgumentParser(
        prog="oauth-gateway add-client",
        description="Register a confidential OAuth client and print its credentials once",
    )
    parser.add_argument("--name", required=True, help="short label, e.g. ha")
    parser.add_argument(
        "--redirect-uri", required=True, action="append", help="exact https:// URI (repeatable)"
    )
    _db_arg(parser)
    args = parser.parse_args(argv)

    if not _CLIENT_NAME.match(args.name):
        parser.error("--name must be lowercase letters, digits and dashes (max 32)")
    if not all(u.startswith("https://") for u in args.redirect_uri):
        parser.error("--redirect-uri must be an https:// URI")
    if not args.db:
        parser.error("--db (or GATEWAY_DB_PATH) is required")

    client_id = f"{args.name}-{secrets.token_urlsafe(12)}"
    client_secret = secrets.token_urlsafe(32)
    conn = db.connect(args.db)
    try:
        db.create_client(
            conn, client_id, args.redirect_uri, name=args.name, client_secret=client_secret
        )
    finally:
        conn.close()
    print(f"client_id:     {client_id}")
    print(f"client_secret: {client_secret}")
    print("The secret is shown only this once; it is stored as a hash.")


def _db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db", default=os.environ.get("GATEWAY_DB_PATH"), help="default: $GATEWAY_DB_PATH"
    )


def list_clients(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="oauth-gateway list-clients", description="List hand-added confidential clients"
    )
    _db_arg(parser)
    args = parser.parse_args(argv)
    if not args.db:
        parser.error("--db (or GATEWAY_DB_PATH) is required")
    conn = db.connect(args.db)
    try:
        for c in db.list_confidential_clients(conn):
            created = time.strftime("%Y-%m-%d", time.gmtime(c["created_at"]))
            print(f"{c['client_id']}  {c['name']}  {created}  {' '.join(c['redirect_uris'])}")
    finally:
        conn.close()


def remove_client(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="oauth-gateway remove-client",
        description="Remove a hand-added confidential client. Tokens already minted for it "
        "stay valid until revoked with `ssh exe.dev ssh-key remove <label>`.",
    )
    parser.add_argument("client_id")
    _db_arg(parser)
    args = parser.parse_args(argv)
    if not args.db:
        parser.error("--db (or GATEWAY_DB_PATH) is required")
    conn = db.connect(args.db)
    try:
        removed = db.remove_confidential_client(conn, args.client_id)
    finally:
        conn.close()
    if not removed:
        print(f"oauth-gateway: no confidential client {args.client_id!r}", file=sys.stderr)
        sys.exit(1)
    print(f"removed {args.client_id}")
    print("Already-minted tokens still work: revoke them with `ssh exe.dev ssh-key list`")
    print("(labels oauth-gateway-<vm>-<name>-...) and `ssh exe.dev ssh-key remove <label>`.")


_SUBCOMMANDS = {
    "add-client": add_client,
    "list-clients": list_clients,
    "remove-client": remove_client,
}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] and argv[0] in _SUBCOMMANDS:
        _SUBCOMMANDS[argv[0]](argv[1:])
        return

    parser = argparse.ArgumentParser(
        prog="oauth-gateway",
        description="OAuth 2.1 + DCR gateway bridging Claude.ai's connector to brain-mcp",
        epilog="Subcommands for hand-added confidential clients: add-client, list-clients, "
        "remove-client (each takes --help).",
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

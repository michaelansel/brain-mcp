"""Wires the OAuth gateway's routes, DB, and HTTP client together."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from oauth_gateway import db
from oauth_gateway.authorize import authorize
from oauth_gateway.callback import github_callback
from oauth_gateway.config import GatewayConfig
from oauth_gateway.dcr import register_client
from oauth_gateway.discovery import authorization_server_metadata, protected_resource_metadata
from oauth_gateway.token import issue_token


def build_app(config: GatewayConfig, http_client: httpx.AsyncClient | None = None) -> Starlette:
    """`http_client`, if provided, is used as-is (and never closed by the app) — tests inject a
    mock transport this way. In production, one is created and torn down by the lifespan."""

    owns_http_client = http_client is None

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        app.state.config = config
        app.state.db = db.connect(config.db_path)
        app.state.http_client = http_client or httpx.AsyncClient(timeout=10.0)
        try:
            yield
        finally:
            if owns_http_client:
                await app.state.http_client.aclose()
            app.state.db.close()

    routes = [
        Route(
            "/.well-known/oauth-authorization-server",
            authorization_server_metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource",
            protected_resource_metadata,
            methods=["GET"],
        ),
        Route("/register", register_client, methods=["POST"]),
        Route("/authorize", authorize, methods=["GET"]),
        Route("/auth/github/callback", github_callback, methods=["GET"]),
        Route("/token", issue_token, methods=["POST"]),
    ]

    return Starlette(routes=routes, lifespan=lifespan)

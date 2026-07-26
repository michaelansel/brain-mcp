"""Dynamic Client Registration (RFC 7591) — public clients only, PKCE is the security boundary."""

from __future__ import annotations

import secrets
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from oauth_gateway import db
from oauth_gateway.config import GatewayConfig
from oauth_gateway.errors import oauth_error


async def register_client(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        # Starlette's request.json() ignores Content-Type entirely, which would
        # otherwise make this endpoint postable from a plain HTML form (CSRF).
        return oauth_error(400, "invalid_client_metadata", "Content-Type must be application/json")

    try:
        body = await request.json()
    except ValueError:
        return oauth_error(400, "invalid_client_metadata", "request body is not valid JSON")

    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return oauth_error(400, "invalid_client_metadata", "redirect_uris is required")

    config: GatewayConfig = request.app.state.config
    allowed = config.allowed_redirect_uris
    if not all(isinstance(u, str) and u in allowed for u in redirect_uris):
        # This is the actual security boundary against a third party registering
        # their own client and having an authorization code delivered to them —
        # PKCE and exact redirect_uri matching only protect a code once it's
        # already bound to a client that could legitimately receive it.
        return oauth_error(400, "invalid_client_metadata", "redirect_uris must be an allowed URI")

    client_id = secrets.token_urlsafe(24)
    conn = request.app.state.db
    db.create_client(conn, client_id, redirect_uris)

    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
        status_code=201,
    )

"""GET /authorize — validates the MCP client's request, then hands off to GitHub."""

from __future__ import annotations

import secrets

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from oauth_gateway import db, github_oauth
from oauth_gateway.config import GatewayConfig
from oauth_gateway.errors import oauth_error


async def authorize(request: Request) -> Response:
    params = request.query_params
    config: GatewayConfig = request.app.state.config
    conn = request.app.state.db

    if params.get("response_type") != "code":
        return oauth_error(400, "unsupported_response_type", "only response_type=code is supported")

    if params.get("code_challenge_method") != "S256":
        return oauth_error(400, "invalid_request", "code_challenge_method=S256 is required")

    code_challenge = params.get("code_challenge")
    if not code_challenge:
        return oauth_error(400, "invalid_request", "code_challenge is required")

    client_id = params.get("client_id")
    if not client_id:
        return oauth_error(400, "invalid_request", "client_id is required")
    client = db.get_client(conn, client_id)
    if client is None:
        return oauth_error(400, "invalid_client", "unknown client_id")

    redirect_uri = params.get("redirect_uri")
    if not redirect_uri or redirect_uri not in client["redirect_uris"]:
        # Don't redirect on a bad redirect_uri — that's the open-redirect case
        # PKCE/exact-match exists to prevent.
        return oauth_error(400, "invalid_request", "redirect_uri does not match a registered value")

    session_id = secrets.token_urlsafe(32)
    db.create_pending_authorization(
        conn,
        session_id=session_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        client_state=params.get("state"),
        ttl_seconds=config.pending_authorization_ttl_seconds,
    )

    github_redirect_uri = f"{config.issuer_url}/auth/github/callback"
    return RedirectResponse(
        github_oauth.build_authorize_url(
            client_id=config.github_client_id,
            redirect_uri=github_redirect_uri,
            state=session_id,
        ),
        status_code=302,
    )

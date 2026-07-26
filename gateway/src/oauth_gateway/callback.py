"""GET /auth/github/callback — completes the GitHub login, mints a single-use auth code."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from oauth_gateway import db, github_oauth
from oauth_gateway.config import GatewayConfig
from oauth_gateway.errors import oauth_error
from oauth_gateway.github_oauth import GitHubAuthError


async def github_callback(request: Request) -> Response:
    params = request.query_params
    config: GatewayConfig = request.app.state.config
    conn = request.app.state.db

    session_id = params.get("state")
    if not session_id:
        return oauth_error(400, "invalid_request", "missing state")

    pending = db.pop_pending_authorization(conn, session_id)
    if pending is None:
        return oauth_error(400, "invalid_request", "authorization session expired or unknown")

    code = params.get("code")
    if not code:
        return oauth_error(400, "invalid_request", "missing code")

    http_client = request.app.state.http_client
    try:
        github_token = await github_oauth.exchange_code(
            http_client,
            client_id=config.github_client_id,
            client_secret=config.github_client_secret,
            code=code,
            redirect_uri=f"{config.issuer_url}/auth/github/callback",
        )
        identity = await github_oauth.fetch_identity(http_client, github_token)
    except (GitHubAuthError, httpx.HTTPError):
        return oauth_error(502, "server_error", "GitHub login could not be completed")

    if not github_oauth.is_allowed(identity, config.allowed_github_ids):
        return PlainTextResponse(
            "Access denied: this GitHub account is not authorized.", status_code=403
        )

    auth_code = secrets.token_urlsafe(32)
    db.create_auth_code(
        conn,
        code=auth_code,
        client_id=pending["client_id"],
        redirect_uri=pending["redirect_uri"],
        code_challenge=pending["code_challenge"],
        github_login=identity.login,
        ttl_seconds=config.auth_code_ttl_seconds,
    )

    redirect_params = {"code": auth_code}
    if pending["client_state"]:
        redirect_params["state"] = pending["client_state"]
    return RedirectResponse(
        f"{pending['redirect_uri']}?{urlencode(redirect_params)}",
        status_code=302,
    )

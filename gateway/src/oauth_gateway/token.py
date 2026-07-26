"""POST /token — authorization_code grant only. No refresh_token grant is supported."""

from __future__ import annotations

import base64
import hashlib

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from oauth_gateway import db, exedev
from oauth_gateway.config import GatewayConfig
from oauth_gateway.errors import oauth_error
from oauth_gateway.exedev import ExeDevMintError


def _pkce_matches(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge


async def issue_token(request: Request) -> Response:
    form = await request.form()
    config: GatewayConfig = request.app.state.config
    conn = request.app.state.db

    if form.get("grant_type") != "authorization_code":
        return oauth_error(400, "unsupported_grant_type", "only authorization_code is supported")

    code = form.get("code")
    code_verifier = form.get("code_verifier")
    client_id = form.get("client_id")
    redirect_uri = form.get("redirect_uri")
    required = (code, code_verifier, client_id, redirect_uri)
    if not all(isinstance(v, str) and v for v in required):
        # Also rejects a multipart file part (form.get returns an UploadFile, not str)
        # masquerading as one of these fields.
        return oauth_error(
            400, "invalid_request", "code, code_verifier, client_id, redirect_uri are required"
        )

    record = db.redeem_auth_code(conn, code)
    if record is None:
        return oauth_error(400, "invalid_grant", "code is invalid, expired, or already used")

    if record["client_id"] != client_id or record["redirect_uri"] != redirect_uri:
        return oauth_error(400, "invalid_grant", "client_id/redirect_uri do not match request")

    try:
        pkce_ok = _pkce_matches(code_verifier, record["code_challenge"])
    except UnicodeEncodeError:
        return oauth_error(400, "invalid_request", "code_verifier must be ASCII")
    if not pkce_ok:
        return oauth_error(400, "invalid_grant", "code_verifier does not match code_challenge")

    http_client = request.app.state.http_client
    try:
        access_token = await exedev.mint_vm_token(
            http_client,
            exedev_api_token=config.exedev_api_token,
            vm_name=config.vm_name,
            ttl_seconds=config.vm_token_ttl_seconds,
            github_login=record["github_login"],
        )
    except ExeDevMintError:
        return oauth_error(502, "server_error", "could not mint an access token")

    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": config.vm_token_ttl_seconds,
        }
    )

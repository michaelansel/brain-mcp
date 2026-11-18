"""POST /token — the authorization_code grant.

Public clients (registered through DCR) authenticate with PKCE alone. Confidential
clients (added with `oauth-gateway add-client`) send their secret in the body
(client_secret_post); PKCE is checked for them only when they sent a challenge at
/authorize.

There is no working refresh. Confidential clients are handed a refresh_token anyway,
because Home Assistant's OAuth helper crashes at expiry without one; it is never stored,
and every refresh_token grant answers `invalid_grant`, which Home Assistant turns into a
clean "reauthenticate" prompt. (While the VM is private a refresh can't reach the
gateway at all — exe.dev's edge sends it to its login page — so a real refresh grant
would only help inside a public window.)
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from oauth_gateway import db, exedev
from oauth_gateway.config import GatewayConfig
from oauth_gateway.errors import oauth_error
from oauth_gateway.exedev import ExeDevMintError

logger = logging.getLogger(__name__)


def _pkce_matches(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge


def _str_field(form, name: str) -> str | None:
    """A form field's value, or None if absent, empty, or not a plain string (a multipart
    file part comes back as an UploadFile, not str)."""
    value = form.get(name)
    return value if isinstance(value, str) and value else None


async def issue_token(request: Request) -> Response:
    form = await request.form()
    config: GatewayConfig = request.app.state.config
    conn = request.app.state.db

    grant_type = form.get("grant_type")
    if grant_type not in ("authorization_code", "refresh_token"):
        return oauth_error(400, "unsupported_grant_type", "only authorization_code is supported")

    # Client authentication. A confidential client must present its secret; a public
    # client must not present one (it has none to match).
    client_id = _str_field(form, "client_id")
    client_secret = _str_field(form, "client_secret")
    client = db.get_client(conn, client_id) if client_id else None
    if grant_type == "refresh_token" and (client is None or not client["confidential"]):
        # Unchanged for DCR clients: they were never given a refresh token.
        return oauth_error(400, "unsupported_grant_type", "only authorization_code is supported")
    if client is None:
        return oauth_error(401, "invalid_client", "unknown client")
    needs_secret = client["confidential"] or client_secret is not None
    if needs_secret and (
        client_secret is None or not db.client_secret_matches(conn, client_id, client_secret)
    ):
        return oauth_error(401, "invalid_client", "client authentication failed")

    if grant_type == "refresh_token":
        return oauth_error(400, "invalid_grant", "refresh is not supported; reauthorize")

    code = _str_field(form, "code")
    redirect_uri = _str_field(form, "redirect_uri")
    if not code or not redirect_uri:
        return oauth_error(400, "invalid_request", "code and redirect_uri are required")
    code_verifier = form.get("code_verifier")
    if code_verifier is not None and not isinstance(code_verifier, str):
        return oauth_error(400, "invalid_request", "code_verifier must be a string")

    record = db.redeem_auth_code(conn, code)
    if record is None:
        return oauth_error(400, "invalid_grant", "code is invalid, expired, or already used")

    if record["client_id"] != client_id or record["redirect_uri"] != redirect_uri:
        return oauth_error(400, "invalid_grant", "client_id/redirect_uri do not match request")

    if record["code_challenge"]:
        if not code_verifier:
            return oauth_error(400, "invalid_request", "code_verifier is required")
        try:
            pkce_ok = _pkce_matches(code_verifier, record["code_challenge"])
        except UnicodeEncodeError:
            return oauth_error(400, "invalid_request", "code_verifier must be ASCII")
        if not pkce_ok:
            return oauth_error(400, "invalid_grant", "code_verifier does not match code_challenge")
    elif not client["confidential"]:
        # /authorize never issues a challenge-less code to a public client; refuse one
        # here anyway rather than let a public client through with no proof at all.
        return oauth_error(400, "invalid_grant", "code has no PKCE challenge")

    github_login = record["github_login"]
    try:
        access_token = await exedev.mint_vm_token(
            request.app.state.http_client,
            exedev_api_token=config.exedev_api_token,
            vm_name=config.vm_name,
            ttl_seconds=config.vm_token_ttl_seconds,
            github_login=github_login,
            client_name=client["name"],
        )
    except ExeDevMintError:
        # Logged at the point of failure since the client only ever sees a bare
        # 502 — without this, diagnosing a minting failure means manually
        # reproducing the exe.dev /exec call by hand.
        logger.exception("failed to mint an exe.dev token for github_login=%s", github_login)
        return oauth_error(502, "server_error", "could not mint an access token")

    logger.info(
        "issued an exe.dev VM token for github_login=%s client=%s",
        github_login,
        client["name"] or client_id,
    )
    body = {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": int(config.vm_token_ttl_seconds),
    }
    if client["confidential"]:
        body["refresh_token"] = secrets.token_urlsafe(32)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})

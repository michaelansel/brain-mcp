"""Auth seam for the HTTP transport.

Everything auth-related lives in this module so tool code never changes when
the auth story does. Two modes, selected by config (mutually exclusive):

- `BRAIN_MCP_TOKEN`: static bearer token, verified in-app. For direct
  exposure or generic reverse proxies.
- `BRAIN_MCP_EXEDEV_EMAILS`: for deployment behind exe.dev's *private*
  HTTPS proxy. The exe.dev edge authenticates every request (browser login
  or signed VM tokens) before it reaches the VM and injects the caller's
  identity as `X-ExeDev-Email`. This layer authorizes against an email
  allowlist — a second, independent check with no secret to store. It fails
  closed: if the proxy is ever flipped public, the header disappears and
  every request is denied.

A FastMCP `OAuthProxy` (or `JWTVerifier` against an external IdP) can slot
in here later the same way. stdio ignores auth entirely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp.server.auth import AuthProvider, StaticTokenVerifier
from starlette.middleware import Middleware
from starlette.responses import JSONResponse

from brain_mcp.config import BrainConfig

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

_EMAIL_HEADER = b"x-exedev-email"


def build_auth(config: BrainConfig) -> AuthProvider | None:
    """Token verifier for FastMCP, or None when auth is off or handled
    by the HTTP middleware layer."""
    if not config.token:
        return None
    return StaticTokenVerifier(tokens={config.token: {"client_id": "brain-mcp-user", "scopes": []}})


def build_http_middleware(config: BrainConfig) -> list[Middleware]:
    """ASGI middleware for the HTTP transport."""
    if config.exedev_emails:
        return [Middleware(ExeDevHeaderAuthMiddleware, allowed_emails=config.exedev_emails)]
    return []


class ExeDevHeaderAuthMiddleware:
    """Authorize requests by the identity the exe.dev edge proxy injected.

    Requires exactly one `X-ExeDev-Email` header (a duplicate suggests
    header smuggling — reject) whose value is on the allowlist. The proxy
    strips client-supplied `X-ExeDev-*` headers, so the value is
    trustworthy only when the service is reachable solely through a
    private exe.dev proxy; verify that at deploy time.
    """

    def __init__(self, app: ASGIApp, allowed_emails: frozenset[str]):
        self.app = app
        self.allowed = frozenset(e.strip().casefold() for e in allowed_emails)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = [v for k, v in scope["headers"] if k == _EMAIL_HEADER]
        if len(values) != 1 or values[0].decode("latin-1").strip().casefold() not in self.allowed:
            response = JSONResponse(
                {
                    "error": "unauthorized",
                    "error_description": "request lacks an authorized exe.dev identity",
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

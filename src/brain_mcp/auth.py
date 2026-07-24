"""Auth seam for the HTTP transport.

Everything auth-related lives behind `build_auth` so a FastMCP `OAuthProxy`
(or any other AuthProvider) can replace the static bearer token without
touching tool code. stdio ignores auth entirely.
"""

from __future__ import annotations

from fastmcp.server.auth import AuthProvider, StaticTokenVerifier

from brain_mcp.config import BrainConfig


def build_auth(config: BrainConfig) -> AuthProvider | None:
    if not config.token:
        return None
    return StaticTokenVerifier(tokens={config.token: {"client_id": "brain-mcp-user", "scopes": []}})

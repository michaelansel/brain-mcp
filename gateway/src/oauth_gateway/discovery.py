"""OAuth discovery metadata (RFC 8414 authorization server, RFC 9728 protected resource).

Gateway and resource share one origin (nginx routes both to the same host), so
`authorization_servers` in the protected-resource document just points back at
this same issuer.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from oauth_gateway.config import GatewayConfig


async def authorization_server_metadata(request: Request) -> JSONResponse:
    config: GatewayConfig = request.app.state.config
    issuer = config.issuer_url
    return JSONResponse(
        {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "registration_endpoint": f"{issuer}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )


async def protected_resource_metadata(request: Request) -> JSONResponse:
    config: GatewayConfig = request.app.state.config
    issuer = config.issuer_url
    return JSONResponse(
        {
            "resource": f"{issuer}/mcp",
            "authorization_servers": [issuer],
        }
    )

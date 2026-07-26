"""Shared OAuth-shaped error responses."""

from __future__ import annotations

from starlette.responses import JSONResponse


def oauth_error(status_code: int, error: str, description: str | None = None) -> JSONResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=status_code)

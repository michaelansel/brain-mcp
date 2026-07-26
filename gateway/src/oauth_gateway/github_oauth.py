"""Thin GitHub OAuth client — login only, no app-level tokens stored beyond the handshake."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"


class GitHubAuthError(Exception):
    """Raised when GitHub's OAuth flow fails or returns something we don't trust."""


@dataclass(frozen=True)
class GitHubIdentity:
    id: str  # immutable numeric GitHub user id — the actual security boundary
    login: str  # current username, for audit/ctx purposes only; renameable and reclaimable


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user",
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    http_client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> str:
    """Exchange an authorization code for a GitHub access token."""
    response = await http_client.post(
        _TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    response.raise_for_status()
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise GitHubAuthError(f"GitHub token exchange did not return an access_token: {payload}")
    return access_token


async def fetch_identity(
    http_client: httpx.AsyncClient, github_access_token: str
) -> GitHubIdentity:
    """Return the authenticated GitHub user's immutable id and current login."""
    response = await http_client.get(
        _USER_URL,
        headers={
            "Authorization": f"Bearer {github_access_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    payload = response.json()
    user_id = payload.get("id")
    login = payload.get("login")
    if user_id is None or not login:
        raise GitHubAuthError(f"GitHub user response is missing id/login: {payload}")
    return GitHubIdentity(id=str(user_id), login=login)


def is_allowed(identity: GitHubIdentity, allowed_ids: frozenset[str]) -> bool:
    # Deliberately keyed on the immutable numeric id, not the login: usernames
    # are renameable and a released handle can be claimed by anyone.
    return identity.id in allowed_ids

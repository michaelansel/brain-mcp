"""Shared test fixtures: an in-memory-ish config and a mocked httpx client standing in for
GitHub and exe.dev, so no test ever touches the network."""

from __future__ import annotations

import httpx
import pytest

from oauth_gateway.config import GatewayConfig

ALLOWED_LOGIN = "octocat"
ALLOWED_ID = "12345"
MINTED_TOKEN = "exe1.minted-test-token"
# The exact, documented redirect_uri for Claude.ai's hosted surfaces (web, Desktop,
# mobile, Cowork) — https://claude.com/docs/connectors/building/authentication
CLAUDE_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def config(tmp_path):
    return GatewayConfig(
        github_client_id="gh-client-id",
        github_client_secret="gh-client-secret",
        allowed_github_ids=frozenset({ALLOWED_ID}),
        allowed_redirect_uris=frozenset({CLAUDE_REDIRECT_URI}),
        exedev_api_token="exedev-minting-token",
        vm_name="test-vm",
        issuer_url="https://test-vm.exe.xyz",
        db_path=str(tmp_path / "gateway.db"),
        vm_token_ttl_seconds=31536000,
        auth_code_ttl_seconds=60,
        pending_authorization_ttl_seconds=600,
    )


def make_mock_http_client(
    *,
    github_login: str = ALLOWED_LOGIN,
    github_id: str = ALLOWED_ID,
    minted_token: str = MINTED_TOKEN,
):
    """A mocked httpx.AsyncClient standing in for both github.com/api.github.com and exe.dev."""

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gh-access-token"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"login": github_login, "id": int(github_id)})
        if url == "https://exe.dev/exec":
            return httpx.Response(200, json={"token": minted_token})
        raise AssertionError(f"unexpected request to {url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def mock_http_client():
    return make_mock_http_client()

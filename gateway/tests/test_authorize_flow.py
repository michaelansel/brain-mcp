import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from oauth_gateway.app import build_app
from tests.conftest import (
    ALLOWED_ID,
    ALLOWED_LOGIN,
    CLAUDE_REDIRECT_URI,
    MINTED_TOKEN,
    make_mock_http_client,
)

REDIRECT_URI = CLAUDE_REDIRECT_URI


def pkce_pair():
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _register(client) -> str:
    r = client.post("/register", json={"redirect_uris": [REDIRECT_URI]})
    assert r.status_code == 201
    return r.json()["client_id"]


def test_authorize_rejects_unknown_client(config):
    app = build_app(config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        r = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "does-not-exist",
                "redirect_uri": REDIRECT_URI,
                "code_challenge": "x",
                "code_challenge_method": "S256",
            },
        )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client"


def test_authorize_rejects_mismatched_redirect_uri(config):
    app = build_app(config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        client_id = _register(client)
        r = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "https://attacker.example/callback",
                "code_challenge": "x",
                "code_challenge_method": "S256",
            },
        )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_authorize_requires_s256(config):
    app = build_app(config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        client_id = _register(client)
        r = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "code_challenge": "x",
                "code_challenge_method": "plain",
            },
        )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_full_happy_path_issues_exedev_token(config):
    app = build_app(
        config, http_client=make_mock_http_client(github_login=ALLOWED_LOGIN, github_id=ALLOWED_ID)
    )
    with TestClient(app) as client:
        client_id = _register(client)
        verifier, challenge = pkce_pair()
        claude_state = "claude-state-123"

        r = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": claude_state,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        github_redirect = _query(r.headers["location"])
        session_id = github_redirect["state"]
        assert r.headers["location"].startswith("https://github.com/login/oauth/authorize")

        r = client.get(
            "/auth/github/callback",
            params={"code": "fake-github-code", "state": session_id},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"].startswith(REDIRECT_URI)
        callback_params = _query(r.headers["location"])
        assert callback_params["state"] == claude_state
        auth_code = callback_params["code"]

        r = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "code_verifier": verifier,
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == MINTED_TOKEN
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == config.vm_token_ttl_seconds
    assert "refresh_token" not in body


def test_unauthorized_github_login_is_denied(config):
    # id, not login, is the security boundary — a non-allowlisted id is denied
    # even if the login string happens to look familiar.
    app = build_app(
        config, http_client=make_mock_http_client(github_login=ALLOWED_LOGIN, github_id="99999")
    )
    with TestClient(app) as client:
        client_id = _register(client)
        _, challenge = pkce_pair()
        r = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        session_id = _query(r.headers["location"])["state"]
        r = client.get(
            "/auth/github/callback",
            params={"code": "fake-github-code", "state": session_id},
            follow_redirects=False,
        )
    assert r.status_code == 403

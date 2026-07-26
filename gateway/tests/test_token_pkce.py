import dataclasses

from starlette.testclient import TestClient

from oauth_gateway.app import build_app
from tests.conftest import make_mock_http_client
from tests.test_authorize_flow import REDIRECT_URI, _query, _register, pkce_pair


def _get_auth_code(client, verifier_challenge=None) -> tuple[str, str, str]:
    """Drives authorize -> github callback, returns (client_id, verifier, auth_code)."""
    client_id = _register(client)
    verifier, challenge = verifier_challenge or pkce_pair()
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
    auth_code = _query(r.headers["location"])["code"]
    return client_id, verifier, auth_code


def test_wrong_code_verifier_rejected(config):
    app = build_app(config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        client_id, _verifier, auth_code = _get_auth_code(client)
        r = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "code_verifier": "wrong-verifier-entirely",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
            },
        )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_code_reuse_rejected(config):
    app = build_app(config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        client_id, verifier, auth_code = _get_auth_code(client)
        token_request = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
        }
        first = client.post("/token", data=token_request)
        second = client.post("/token", data=token_request)
    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_expired_code_rejected(config):
    expired_config = dataclasses.replace(config, auth_code_ttl_seconds=-10)
    app = build_app(expired_config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        client_id, verifier, auth_code = _get_auth_code(client)
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
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_refresh_token_grant_rejected(config):
    app = build_app(config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": "whatever"})
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"

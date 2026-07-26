from starlette.testclient import TestClient

from oauth_gateway.app import build_app
from tests.conftest import CLAUDE_REDIRECT_URI, make_mock_http_client


def _client(config):
    return TestClient(build_app(config, http_client=make_mock_http_client()))


def test_registration_persists_and_returns_client(config):
    with _client(config) as client:
        r = client.post("/register", json={"redirect_uris": [CLAUDE_REDIRECT_URI]})
    assert r.status_code == 201
    body = r.json()
    assert body["client_id"]
    assert body["redirect_uris"] == [CLAUDE_REDIRECT_URI]
    assert body["token_endpoint_auth_method"] == "none"
    assert body["grant_types"] == ["authorization_code"]


def test_registration_requires_redirect_uris(config):
    with _client(config) as client:
        r = client.post("/register", json={})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client_metadata"


def test_registration_rejects_non_https_redirect_uri(config):
    with _client(config) as client:
        r = client.post("/register", json={"redirect_uris": ["http://claude.ai/callback"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client_metadata"


def test_registration_rejects_malformed_json(config):
    with _client(config) as client:
        r = client.post(
            "/register", content=b"not json", headers={"Content-Type": "application/json"}
        )
    assert r.status_code == 400


def test_registration_rejects_redirect_uri_outside_allowlist(config):
    # This is the actual security boundary: a third party registering their own
    # client with their own redirect_uri must never succeed, or an authorization
    # code (and the exe.dev token it turns into) could end up delivered to them
    # instead of the real client.
    with _client(config) as client:
        r = client.post("/register", json={"redirect_uris": ["https://attacker.example/callback"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client_metadata"


def test_registration_rejects_near_match_redirect_uri(config):
    # Exact match, not prefix match: something that merely starts with (or
    # extends) the allowed URI must not slip through.
    with _client(config) as client:
        r = client.post("/register", json={"redirect_uris": [CLAUDE_REDIRECT_URI + "/../evil"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client_metadata"


def test_registration_requires_json_content_type(config):
    with _client(config) as client:
        r = client.post(
            "/register",
            content=f'{{"redirect_uris": ["{CLAUDE_REDIRECT_URI}"]}}'.encode(),
            headers={"Content-Type": "text/plain"},
        )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client_metadata"

"""Configuration from environment. Fails loudly at startup on missing required vars."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_MIN_VM_TOKEN_TTL_SECONDS = 86400  # exe.dev's --exp only has day granularity or coarser


class ConfigError(Exception):
    """Raised at startup when the environment is unusable."""


@dataclass(frozen=True)
class GatewayConfig:
    github_client_id: str
    github_client_secret: str
    allowed_github_ids: frozenset[str]  # GitHub's immutable numeric id, not the renameable login
    allowed_redirect_uris: frozenset[str]  # exact match — only these may ever complete a flow
    exedev_api_token: str
    vm_name: str
    issuer_url: str  # public origin, e.g. https://myvm.exe.xyz — no trailing slash
    db_path: str
    vm_token_ttl_seconds: int = 31536000  # 1 year
    auth_code_ttl_seconds: int = 60
    pending_authorization_ttl_seconds: int = 600  # 10 minutes
    host: str = "127.0.0.1"
    port: int = 9000


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def load_config(env: Mapping[str, str] | None = None) -> GatewayConfig:
    if env is None:
        env = os.environ

    github_client_id = _require(env, "GATEWAY_GITHUB_CLIENT_ID")
    github_client_secret = _require(env, "GATEWAY_GITHUB_CLIENT_SECRET")
    exedev_api_token = _require(env, "GATEWAY_EXEDEV_API_TOKEN")
    vm_name = _require(env, "GATEWAY_VM_NAME")
    issuer_url = _require(env, "GATEWAY_ISSUER_URL").rstrip("/")
    db_path = _require(env, "GATEWAY_DB_PATH")

    ids_raw = env.get("GATEWAY_ALLOWED_GITHUB_IDS", "")
    allowed_github_ids = frozenset(gh_id.strip() for gh_id in ids_raw.split(",") if gh_id.strip())
    if not allowed_github_ids:
        raise ConfigError("GATEWAY_ALLOWED_GITHUB_IDS is not set; refusing to allow no one")

    uris_raw = _require(env, "GATEWAY_ALLOWED_REDIRECT_URIS")
    allowed_redirect_uris = frozenset(u.strip() for u in uris_raw.split(",") if u.strip())
    all_https = all(u.startswith("https://") for u in allowed_redirect_uris)
    if not allowed_redirect_uris or not all_https:
        raise ConfigError(
            "GATEWAY_ALLOWED_REDIRECT_URIS must be a comma-separated list of exact https:// URIs"
        )

    def _int_env(name: str, default: int) -> int:
        raw = env.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} '{raw}' is not a number") from exc

    vm_token_ttl_seconds = _int_env("GATEWAY_VM_TOKEN_TTL_SECONDS", 31536000)
    if vm_token_ttl_seconds < _MIN_VM_TOKEN_TTL_SECONDS:
        # exe.dev's --exp is day-granularity or coarser (see exedev.py) — a TTL below one
        # day would silently round UP to a full day, which is the opposite of what
        # someone tightening this value would expect.
        raise ConfigError(
            f"GATEWAY_VM_TOKEN_TTL_SECONDS must be at least {_MIN_VM_TOKEN_TTL_SECONDS} "
            "(exe.dev's --exp has day granularity)"
        )

    return GatewayConfig(
        github_client_id=github_client_id,
        github_client_secret=github_client_secret,
        allowed_github_ids=allowed_github_ids,
        allowed_redirect_uris=allowed_redirect_uris,
        exedev_api_token=exedev_api_token,
        vm_name=vm_name,
        issuer_url=issuer_url,
        db_path=db_path,
        vm_token_ttl_seconds=vm_token_ttl_seconds,
        auth_code_ttl_seconds=_int_env("GATEWAY_AUTH_CODE_TTL_SECONDS", 60),
        pending_authorization_ttl_seconds=_int_env(
            "GATEWAY_PENDING_AUTHORIZATION_TTL_SECONDS", 600
        ),
        host=env.get("GATEWAY_HOST", "127.0.0.1"),
        port=_int_env("GATEWAY_PORT", 9000),
    )

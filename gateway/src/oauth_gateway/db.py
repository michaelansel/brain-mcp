"""SQLite persistence: registered clients, in-flight authorizations, single-use auth codes.

Two kinds of client share `oauth_clients`. A public client registered itself through
DCR (Claude.ai) and has no secret; PKCE is its proof. A confidential client was added
by hand with `oauth-gateway add-client` and has a secret, stored only as a hash.

No refresh tokens are stored — the minted exe.dev VM token is long-lived (see
config.py), and expiry is handled by a manual reconnect, not an automated refresh.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    redirect_uris TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_authorizations (
    session_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    client_state TEXT,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    github_login TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Columns added after the first deployment; an existing database gets them here.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(oauth_clients)")}
    for column in ("name", "client_secret_hash"):
        if column not in columns:
            conn.execute(f"ALTER TABLE oauth_clients ADD COLUMN {column} TEXT")
    conn.commit()
    return conn


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_client(
    conn: sqlite3.Connection,
    client_id: str,
    redirect_uris: list[str],
    *,
    name: str | None = None,
    client_secret: str | None = None,
) -> None:
    secret_hash = _hash(client_secret) if client_secret is not None else None
    conn.execute(
        """INSERT INTO oauth_clients
           (client_id, redirect_uris, created_at, name, client_secret_hash)
           VALUES (?, ?, ?, ?, ?)""",
        (client_id, json.dumps(redirect_uris), int(time.time()), name, secret_hash),
    )
    conn.commit()


def get_client(conn: sqlite3.Connection, client_id: str) -> dict | None:
    row = conn.execute(
        """SELECT client_id, redirect_uris, name, client_secret_hash
           FROM oauth_clients WHERE client_id = ?""",
        (client_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "client_id": row["client_id"],
        "redirect_uris": json.loads(row["redirect_uris"]),
        "name": row["name"],
        "confidential": row["client_secret_hash"] is not None,
    }


def client_secret_matches(conn: sqlite3.Connection, client_id: str, client_secret: str) -> bool:
    """True only for a confidential client whose stored hash matches this secret."""
    row = conn.execute(
        "SELECT client_secret_hash FROM oauth_clients WHERE client_id = ?", (client_id,)
    ).fetchone()
    if row is None or row["client_secret_hash"] is None:
        return False
    return hmac.compare_digest(row["client_secret_hash"], _hash(client_secret))


def list_confidential_clients(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT client_id, name, redirect_uris, created_at FROM oauth_clients
           WHERE client_secret_hash IS NOT NULL ORDER BY created_at"""
    ).fetchall()
    return [
        {
            "client_id": r["client_id"],
            "name": r["name"],
            "redirect_uris": json.loads(r["redirect_uris"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def remove_confidential_client(conn: sqlite3.Connection, client_id: str) -> bool:
    """Delete a hand-added client. Its codes in flight die with it, since /token looks
    the client up again. VM tokens already minted for it are NOT revoked here."""
    cursor = conn.execute(
        "DELETE FROM oauth_clients WHERE client_id = ? AND client_secret_hash IS NOT NULL",
        (client_id,),
    )
    conn.commit()
    return cursor.rowcount == 1


def create_pending_authorization(
    conn: sqlite3.Connection,
    session_id: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    client_state: str | None,
    ttl_seconds: int,
) -> None:
    conn.execute(
        """INSERT INTO pending_authorizations
           (session_id, client_id, redirect_uri, code_challenge, client_state, expires_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            client_id,
            redirect_uri,
            code_challenge,
            client_state,
            int(time.time()) + ttl_seconds,
        ),
    )
    conn.commit()


def pop_pending_authorization(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Fetch and delete — single use regardless of outcome."""
    row = conn.execute(
        """SELECT client_id, redirect_uri, code_challenge, client_state, expires_at
           FROM pending_authorizations WHERE session_id = ?""",
        (session_id,),
    ).fetchone()
    conn.execute("DELETE FROM pending_authorizations WHERE session_id = ?", (session_id,))
    conn.commit()
    if row is None or row["expires_at"] < time.time():
        return None
    return {
        "client_id": row["client_id"],
        "redirect_uri": row["redirect_uri"],
        "code_challenge": row["code_challenge"],
        "client_state": row["client_state"],
    }


def create_auth_code(
    conn: sqlite3.Connection,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    github_login: str,
    ttl_seconds: int,
) -> None:
    conn.execute(
        """INSERT INTO auth_codes
           (code_hash, client_id, redirect_uri, code_challenge, github_login, expires_at, used)
           VALUES (?, ?, ?, ?, ?, ?, 0)""",
        (
            _hash(code),
            client_id,
            redirect_uri,
            code_challenge,
            github_login,
            int(time.time()) + ttl_seconds,
        ),
    )
    conn.commit()


def redeem_auth_code(conn: sqlite3.Connection, code: str) -> dict | None:
    """Atomically claim-and-mark-used by hash. Returns None if missing, expired, or already used.

    The UPDATE...WHERE used = 0 + rowcount check is the actual single-use gate — a plain
    read-then-write would have a race between two concurrent redemptions of the same code.
    """
    code_hash = _hash(code)
    cursor = conn.execute(
        "UPDATE auth_codes SET used = 1 WHERE code_hash = ? AND used = 0", (code_hash,)
    )
    conn.commit()
    if cursor.rowcount != 1:
        return None
    row = conn.execute(
        """SELECT client_id, redirect_uri, code_challenge, github_login, expires_at
           FROM auth_codes WHERE code_hash = ?""",
        (code_hash,),
    ).fetchone()
    if row is None or row["expires_at"] < time.time():
        return None
    return {
        "client_id": row["client_id"],
        "redirect_uri": row["redirect_uri"],
        "code_challenge": row["code_challenge"],
        "github_login": row["github_login"],
    }

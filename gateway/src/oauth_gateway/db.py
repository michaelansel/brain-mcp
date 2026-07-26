"""SQLite persistence: registered clients, in-flight authorizations, single-use auth codes.

No refresh tokens — the minted exe.dev VM token is long-lived (see config.py),
and expiry is handled by a manual reconnect, not an automated refresh grant.
"""

from __future__ import annotations

import hashlib
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
    conn.commit()
    return conn


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_client(conn: sqlite3.Connection, client_id: str, redirect_uris: list[str]) -> None:
    conn.execute(
        "INSERT INTO oauth_clients (client_id, redirect_uris, created_at) VALUES (?, ?, ?)",
        (client_id, json.dumps(redirect_uris), int(time.time())),
    )
    conn.commit()


def get_client(conn: sqlite3.Connection, client_id: str) -> dict | None:
    row = conn.execute(
        "SELECT client_id, redirect_uris FROM oauth_clients WHERE client_id = ?", (client_id,)
    ).fetchone()
    if row is None:
        return None
    return {"client_id": row["client_id"], "redirect_uris": json.loads(row["redirect_uris"])}


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

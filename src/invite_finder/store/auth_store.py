"""Operator passcodes and sessions.

Only hashes are persisted. A dump of this database must not yield a usable
passcode or a live session token.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_in(**delta: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _is_past(value: str | None) -> bool:
    if not value:
        return False
    try:
        return datetime.now(timezone.utc) > datetime.fromisoformat(value)
    except ValueError:
        return False


# --- passcodes ---------------------------------------------------------------


def create_passcode(
    conn: sqlite3.Connection,
    *,
    code_hash: str,
    salt: str,
    ttl_minutes: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO admin_passcodes (code_hash, salt, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (code_hash, salt, _iso_in(minutes=ttl_minutes), now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def live_passcodes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unconsumed, unexpired passcodes, newest first."""
    rows = conn.execute(
        "SELECT * FROM admin_passcodes WHERE consumed_at IS NULL ORDER BY id DESC"
    ).fetchall()
    return [r for r in rows if not _is_past(r["expires_at"])]


def seconds_since_last_passcode(conn: sqlite3.Connection) -> float | None:
    """Age of the most recent passcode request, for rate limiting. None if
    there has never been one."""
    row = conn.execute(
        "SELECT created_at FROM admin_passcodes ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    try:
        created = datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - created).total_seconds()


def record_attempt(conn: sqlite3.Connection, passcode_id: int) -> int:
    conn.execute(
        "UPDATE admin_passcodes SET attempts = attempts + 1 WHERE id = ?",
        (passcode_id,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT attempts FROM admin_passcodes WHERE id = ?", (passcode_id,)
    ).fetchone()
    return int(row["attempts"]) if row else 0


def delete_passcode(conn: sqlite3.Connection, passcode_id: int) -> None:
    """Remove a passcode entirely, as opposed to consuming it.

    Used when delivery fails: the code never reached anyone, so it should
    leave no trace — including no rate-limit footprint that would block the
    operator from immediately trying again.
    """
    conn.execute("DELETE FROM admin_passcodes WHERE id = ?", (passcode_id,))
    conn.commit()


def consume_passcode(conn: sqlite3.Connection, passcode_id: int) -> None:
    conn.execute(
        "UPDATE admin_passcodes SET consumed_at = ? WHERE id = ?",
        (now_iso(), passcode_id),
    )
    conn.commit()


def consume_all_passcodes(conn: sqlite3.Connection) -> None:
    """Burn every outstanding code. Used after a successful login, and after
    too many failures, so a code can never be retried."""
    conn.execute(
        "UPDATE admin_passcodes SET consumed_at = ? WHERE consumed_at IS NULL",
        (now_iso(),),
    )
    conn.commit()


# --- sessions ----------------------------------------------------------------


def create_session(
    conn: sqlite3.Connection, *, token_hash: str, ttl_hours: int, label: str | None = None
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO admin_sessions (token_hash, label, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (token_hash, label, _iso_in(hours=ttl_hours), now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_live_session(conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM admin_sessions WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    if row is None or row["revoked_at"] or _is_past(row["expires_at"]):
        return None
    conn.execute(
        "UPDATE admin_sessions SET last_seen_at = ? WHERE id = ?",
        (now_iso(), row["id"]),
    )
    conn.commit()
    return row


def revoke_session(conn: sqlite3.Connection, token_hash: str) -> bool:
    cursor = conn.execute(
        "UPDATE admin_sessions SET revoked_at = ? "
        "WHERE token_hash = ? AND revoked_at IS NULL",
        (now_iso(), token_hash),
    )
    conn.commit()
    return cursor.rowcount > 0


def revoke_all_sessions(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "UPDATE admin_sessions SET revoked_at = ? WHERE revoked_at IS NULL",
        (now_iso(),),
    )
    conn.commit()
    return cursor.rowcount

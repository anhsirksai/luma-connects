"""Passcode-based operator auth.

The API serves names, LinkedIn URLs, emails and phone numbers, and it runs on
hosts whose URLs are unauthenticated by design — a Superserve preview URL or a
cloudflared tunnel is reachable by anyone who learns it. This module gates it
behind a one-time passcode delivered to the operator's own phone over Linq.

Deliberate properties:
  - Codes and session tokens are stored only as salted hashes.
  - Codes are single-use, short-lived, and attempt-limited.
  - Comparison is constant-time.
  - Responses never reveal whether a code existed or was close.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass

from invite_finder.config import Settings
from invite_finder.store import auth_store

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS_PER_CODE = 5
# A new code invalidates the previous one, so this only limits how often an
# attacker can make the operator's phone buzz.
MIN_SECONDS_BETWEEN_REQUESTS = 30


class AuthError(RuntimeError):
    """Raised when a passcode cannot be issued."""


@dataclass(frozen=True)
class IssuedCode:
    code: str
    passcode_id: int


def _hash(value: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


def hash_token(token: str) -> str:
    """Session tokens carry their own entropy, so a fixed salt is enough; the
    point is only that the database never holds the bearer value."""
    return hashlib.sha256(f"session:{token}".encode("utf-8")).hexdigest()


def generate_code() -> str:
    """A six-digit code. Short enough to type from a text message; safe only
    because it is single-use, expires in minutes, and is attempt-limited."""
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_passcode(conn: sqlite3.Connection) -> IssuedCode:
    """Mint a passcode, invalidating any outstanding one.

    Raises AuthError if called again too soon — otherwise anyone who finds the
    URL can make the operator's phone ring indefinitely.
    """
    age = auth_store.seconds_since_last_passcode(conn)
    if age is not None and age < MIN_SECONDS_BETWEEN_REQUESTS:
        raise AuthError(
            f"A passcode was just sent. Try again in "
            f"{int(MIN_SECONDS_BETWEEN_REQUESTS - age)}s."
        )

    # Only one code may be live at a time.
    auth_store.consume_all_passcodes(conn)

    code = generate_code()
    salt = secrets.token_hex(16)
    passcode_id = auth_store.create_passcode(
        conn,
        code_hash=_hash(code, salt),
        salt=salt,
        ttl_minutes=CODE_TTL_MINUTES,
    )
    return IssuedCode(code=code, passcode_id=passcode_id)


def verify_passcode(conn: sqlite3.Connection, submitted: str) -> bool:
    """Check a submitted code against every live passcode.

    Returns a bare bool on purpose: the caller must not be able to tell a
    wrong code from an expired one from no code at all.
    """
    submitted = (submitted or "").strip()
    if not submitted:
        return False

    for row in auth_store.live_passcodes(conn):
        attempts = auth_store.record_attempt(conn, int(row["id"]))
        if attempts > MAX_ATTEMPTS_PER_CODE:
            # Burn everything: a code being guessed at is a code to throw away.
            auth_store.consume_all_passcodes(conn)
            return False

        expected = _hash(submitted, str(row["salt"]))
        if hmac.compare_digest(expected, str(row["code_hash"])):
            # One code, one session. Burn all outstanding codes on success too,
            # so a code observed in transit cannot be replayed.
            auth_store.consume_all_passcodes(conn)
            return True

    return False


def start_session(
    conn: sqlite3.Connection, settings: Settings, *, label: str | None = None
) -> str:
    """Issue a bearer token. Returned once; only its hash is stored."""
    token = secrets.token_urlsafe(32)
    auth_store.create_session(
        conn,
        token_hash=hash_token(token),
        ttl_hours=settings.admin_session_ttl_hours,
        label=label,
    )
    return token


def session_is_valid(conn: sqlite3.Connection, token: str) -> bool:
    if not token:
        return False
    return auth_store.get_live_session(conn, hash_token(token)) is not None


def end_session(conn: sqlite3.Connection, token: str) -> bool:
    return auth_store.revoke_session(conn, hash_token(token))


def bearer_from_header(header_value: str | None) -> str:
    """Accept `Authorization: Bearer <token>`, tolerating a bare token."""
    if not header_value:
        return ""
    value = header_value.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def passcode_message(code: str) -> str:
    return (
        f"Luma Connects admin passcode: {code}\n\n"
        f"Expires in {CODE_TTL_MINUTES} minutes. If you didn't ask for this, "
        f"someone has found your API URL — rotate it."
    )

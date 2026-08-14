from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get(conn: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM http_cache WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE http_cache SET hit_count = hit_count + 1 WHERE fingerprint = ?",
            (fingerprint,),
        )
        conn.commit()
    return row


def put(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    kind: str,
    url: str,
    request_json: str,
    status_code: int,
    content_type: str | None,
    body: str,
    expires_at: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO http_cache
          (fingerprint, kind, url, request_json, status_code, content_type,
           body, byte_size, fetched_at, expires_at, hit_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(fingerprint) DO UPDATE SET
          kind=excluded.kind, url=excluded.url, request_json=excluded.request_json,
          status_code=excluded.status_code, content_type=excluded.content_type,
          body=excluded.body, byte_size=excluded.byte_size,
          fetched_at=excluded.fetched_at, expires_at=excluded.expires_at
        """,
        (
            fingerprint,
            kind,
            url,
            request_json,
            status_code,
            content_type,
            body,
            len(body.encode("utf-8")),
            now_iso(),
            expires_at,
        ),
    )
    conn.commit()


def seed_raw(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    kind: str,
    url: str,
    body: str,
    status_code: int = 200,
    content_type: str = "application/json",
    request_json: str = "{}",
    expires_at: str | None = None,
) -> None:
    """Insert a fixture body directly under a precomputed fingerprint (offline dev)."""
    put(
        conn,
        fingerprint=fingerprint,
        kind=kind,
        url=url,
        request_json=request_json,
        status_code=status_code,
        content_type=content_type,
        body=body,
        expires_at=expires_at,
    )


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(hit_count),0) AS hits FROM http_cache"
    ).fetchone()
    return {"entries": row["n"], "hits": row["hits"]}

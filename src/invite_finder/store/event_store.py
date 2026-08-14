from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_event(conn: sqlite3.Connection, **fields: Any) -> int:
    """Insert or update an event keyed on luma_slug. Returns the event id.

    Accepted fields mirror the `events` table columns (see db.py) minus
    id/created_at/updated_at, which are managed here.
    """
    slug = fields["luma_slug"]
    existing = conn.execute(
        "SELECT id FROM events WHERE luma_slug = ?", (slug,)
    ).fetchone()
    now = now_iso()

    columns = [
        "luma_slug", "luma_api_id", "source_url", "name", "description",
        "cover_url", "start_at", "end_at", "timezone", "location_type",
        "venue_name", "address", "city", "region", "country", "latitude",
        "longitude", "guest_count", "show_guest_list", "categories_json",
        "ingest_source", "ingest_warnings_json", "raw_json",
    ]
    values = [fields.get(c) for c in columns]

    if existing:
        set_clause = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(
            f"UPDATE events SET {set_clause}, updated_at = ? WHERE id = ?",
            (*values, now, existing["id"]),
        )
        conn.commit()
        return int(existing["id"])

    placeholders = ", ".join(["?"] * (len(columns) + 2))
    cursor = conn.execute(
        f"INSERT INTO events ({', '.join(columns)}, created_at, updated_at) "
        f"VALUES ({placeholders})",
        (*values, now, now),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_by_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM events WHERE luma_slug = ?", (slug,)).fetchone()


def get_by_id(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def list_events(
    conn: sqlite3.Connection,
    *,
    start_from: str | None = None,
    start_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    clauses: list[str] = []
    params: list[Any] = []
    if start_from:
        clauses.append("start_at >= ?")
        params.append(start_from)
    if start_to:
        clauses.append("start_at <= ?")
        params.append(start_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM events {where}", params
    ).fetchone()["n"]

    rows = conn.execute(
        f"SELECT * FROM events {where} ORDER BY start_at ASC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return list(rows), int(total)


def delete_event(conn: sqlite3.Connection, event_id: int) -> None:
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()


def categories(row: sqlite3.Row) -> list[str]:
    return json.loads(row["categories_json"] or "[]")


def warnings(row: sqlite3.Row) -> list[str]:
    return json.loads(row["ingest_warnings_json"] or "[]")

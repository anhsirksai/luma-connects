from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_thread(conn: sqlite3.Connection, event_id: int, title: str | None = None) -> int:
    cursor = conn.execute(
        "INSERT INTO chat_threads (event_id, title, created_at) VALUES (?, ?, ?)",
        (event_id, title, now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_thread(conn: sqlite3.Connection, thread_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM chat_threads WHERE id = ?", (thread_id,)
    ).fetchone()


def add_message(
    conn: sqlite3.Connection,
    thread_id: int,
    *,
    role: str,
    content: str,
    filters: dict[str, Any] | None = None,
    cards: list[dict[str, Any]] | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO chat_messages (thread_id, role, content, filters_json,
                                    cards_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            thread_id, role, content,
            json.dumps(filters) if filters is not None else None,
            json.dumps(cards) if cards is not None else None,
            now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_messages(conn: sqlite3.Connection, thread_id: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY id ASC",
        (thread_id,),
    ).fetchall()
    return list(rows)

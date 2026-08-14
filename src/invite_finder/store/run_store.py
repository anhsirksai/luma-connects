from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_run(
    conn: sqlite3.Connection,
    *,
    event_id: int | None,
    input_url: str,
    params: dict[str, Any] | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO runs (event_id, input_url, status, phase, params_json,
                           stats_json, created_at)
        VALUES (?, ?, 'queued', 'queued', ?, '{}', ?)
        """,
        (event_id, input_url, json.dumps(params or {}), now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def set_event_id(conn: sqlite3.Connection, run_id: int, event_id: int) -> None:
    conn.execute("UPDATE runs SET event_id = ? WHERE id = ?", (event_id, run_id))
    conn.commit()


def update_status(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str | None = None,
    phase: str | None = None,
    error: str | None = None,
    stats: dict[str, Any] | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    sets = []
    params: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if phase is not None:
        sets.append("phase = ?")
        params.append(phase)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if stats is not None:
        sets.append("stats_json = ?")
        params.append(json.dumps(stats))
    if started:
        sets.append("started_at = ?")
        params.append(now_iso())
    if finished:
        sets.append("finished_at = ?")
        params.append(now_iso())
    if not sets:
        return
    params.append(run_id)
    conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def latest_run_for_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()


def latest_active_run_for_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT r.* FROM runs r
        WHERE r.input_url LIKE ? AND r.status IN ('queued','running')
        ORDER BY r.created_at DESC LIMIT 1
        """,
        (f"%{slug}%",),
    ).fetchone()


def reap_stuck_runs(conn: sqlite3.Connection) -> int:
    """Mark any run left in queued/running (e.g. after a restart) as failed."""
    cursor = conn.execute(
        """
        UPDATE runs SET status='failed', error='Interrupted by process restart',
          finished_at=?
        WHERE status IN ('queued','running')
        """,
        (now_iso(),),
    )
    conn.commit()
    return cursor.rowcount


def append_event(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    type_: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS m FROM run_events WHERE run_id = ?", (run_id,)
    ).fetchone()
    seq = int(row["m"]) + 1
    conn.execute(
        """
        INSERT INTO run_events (run_id, seq, ts, type, message, data_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, seq, now_iso(), type_, message, json.dumps(data or {})),
    )
    conn.commit()
    return seq


def list_events_after(
    conn: sqlite3.Connection, run_id: int, after_seq: int = 0, limit: int = 500
) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM run_events WHERE run_id = ? AND seq > ?
        ORDER BY seq ASC LIMIT ?
        """,
        (run_id, after_seq, limit),
    ).fetchall()
    return list(rows)


def recent_events(conn: sqlite3.Connection, run_id: int, limit: int = 50) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq DESC LIMIT ?",
        (run_id, limit),
    ).fetchall()
    return list(reversed(rows))

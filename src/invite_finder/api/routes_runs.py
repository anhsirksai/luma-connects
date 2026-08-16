from __future__ import annotations

import asyncio
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from invite_finder import db
from invite_finder.api.deps import get_conn, get_settings, require_admin, require_admin_stream
from invite_finder.api.schemas import RunStatusOut
from invite_finder.api.serialize import build_run_status
from invite_finder.config import Settings
from invite_finder.store import run_store

router = APIRouter(prefix="/api", tags=["runs"])

POLL_INTERVAL_SECONDS = 0.4
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@router.get("/runs/{run_id}", response_model=RunStatusOut, dependencies=[Depends(require_admin)])
def get_run(run_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> RunStatusOut:
    run = run_store.get_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return build_run_status(conn, run)


@router.get("/runs/{run_id}/stream", dependencies=[Depends(require_admin_stream)])
async def stream_run(
    run_id: int,
    after_seq: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> EventSourceResponse:
    # Deliberately not using the Depends(get_conn) generator here: FastAPI
    # closes yield-dependencies as soon as the endpoint function returns,
    # which for a streaming response happens before the stream body is ever
    # consumed. The generator below owns its own connection instead.
    check_conn = db.connect(settings.invite_db_path)
    try:
        run = run_store.get_run(check_conn, run_id)
    finally:
        check_conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    async def event_generator():
        conn = db.connect(settings.invite_db_path)
        try:
            seq = after_seq
            while True:
                rows = run_store.list_events_after(conn, run_id, after_seq=seq)
                for row in rows:
                    seq = row["seq"]
                    yield {
                        "event": row["type"],
                        "data": json.dumps(
                            {
                                "seq": row["seq"],
                                "ts": row["ts"],
                                "message": row["message"],
                                "data": json.loads(row["data_json"] or "{}"),
                            }
                        ),
                    }
                    if row["type"] in ("done", "error"):
                        return

                current = run_store.get_run(conn, run_id)
                if current is not None and current["status"] in TERMINAL_STATUSES:
                    return

                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        finally:
            conn.close()

    return EventSourceResponse(event_generator(), ping=15)

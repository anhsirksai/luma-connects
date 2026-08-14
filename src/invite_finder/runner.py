from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Callable

from invite_finder.pipeline import run_event_pipeline
from invite_finder.protocols import WebDataClient
from invite_finder.store import run_store


class RunReporterImpl:
    """Writes progress directly into the run_events table for one run_id.
    There is no in-memory pub/sub -- SSE consumers poll run_events instead --
    so progress survives a process restart and reconnecting clients."""

    def __init__(self, conn: sqlite3.Connection, run_id: int) -> None:
        self.conn = conn
        self.run_id = run_id

    def emit(self, type_: str, message: str, data: dict[str, Any] | None = None) -> None:
        run_store.append_event(self.conn, self.run_id, type_=type_, message=message, data=data)


BuildClient = Callable[[sqlite3.Connection, RunReporterImpl], WebDataClient]


class RunManager:
    """Tracks in-process asyncio tasks executing pipeline runs. All state that
    matters (status, phase, progress log) lives in the database, not on this
    object -- so a process restart mid-run leaves an honest 'running' row for
    reap_stuck_runs() to clean up at the next boot, rather than losing state."""

    def __init__(self, conn_factory: Callable[[], sqlite3.Connection]) -> None:
        self._conn_factory = conn_factory
        self._tasks: dict[int, asyncio.Task] = {}

    def start(
        self,
        run_id: int,
        *,
        luma_url: str,
        build_client: BuildClient,
        agent_model: str,
        max_profiles: int = 20,
        max_serp_queries: int = 4,
        max_page_fetches: int = 6,
        use_llm_classification: bool = True,
    ) -> asyncio.Task:
        task = asyncio.create_task(
            self._execute(
                run_id,
                luma_url=luma_url,
                build_client=build_client,
                agent_model=agent_model,
                max_profiles=max_profiles,
                max_serp_queries=max_serp_queries,
                max_page_fetches=max_page_fetches,
                use_llm_classification=use_llm_classification,
            )
        )
        self._tasks[run_id] = task
        return task

    async def _execute(
        self,
        run_id: int,
        *,
        luma_url: str,
        build_client: BuildClient,
        agent_model: str,
        max_profiles: int,
        max_serp_queries: int,
        max_page_fetches: int,
        use_llm_classification: bool,
    ) -> None:
        conn = self._conn_factory()
        try:
            run_store.update_status(conn, run_id, status="running", started=True)
            reporter = RunReporterImpl(conn, run_id)
            client = build_client(conn, reporter)
            await run_event_pipeline(
                conn,
                run_id,
                luma_url=luma_url,
                client=client,
                agent_model=agent_model,
                reporter=reporter,
                max_profiles=max_profiles,
                max_serp_queries=max_serp_queries,
                max_page_fetches=max_page_fetches,
                use_llm_classification=use_llm_classification,
            )
        except Exception:
            # run_event_pipeline already records the failure to the DB; swallow
            # here so the background task doesn't produce an "exception was
            # never retrieved" warning.
            pass
        finally:
            conn.close()
            self._tasks.pop(run_id, None)

    def is_active(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

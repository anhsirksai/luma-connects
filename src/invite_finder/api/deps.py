from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from functools import lru_cache

from invite_finder import db
from invite_finder.brightdata import BrightDataClient
from invite_finder.cache import CachingSession
from invite_finder.config import Settings
from invite_finder.conversation import ConversationDeps
from invite_finder.linq import LinqClient
from invite_finder.observability import ObservedBrightDataClient
from invite_finder.protocols import WebDataClient
from invite_finder.runner import RunManager, RunReporterImpl


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def get_conn() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = db.connect(settings.invite_db_path)
    try:
        yield conn
    finally:
        conn.close()


def build_client_for_run(
    conn: sqlite3.Connection, reporter: RunReporterImpl, *, force_refresh: bool = False
) -> WebDataClient:
    settings = get_settings()
    session = CachingSession(conn, offline=settings.invite_offline, force_refresh=force_refresh)
    inner = BrightDataClient(settings, session=session)
    return ObservedBrightDataClient(inner, reporter)


@lru_cache(maxsize=1)
def get_run_manager() -> RunManager:
    settings = get_settings()
    return RunManager(lambda: db.connect(settings.invite_db_path))


@lru_cache(maxsize=1)
def get_linq_client() -> LinqClient:
    return LinqClient(get_settings())


@lru_cache(maxsize=1)
def get_conversation_deps() -> ConversationDeps:
    """Everything the conversation state machine needs.

    conn_factory rather than a connection: background delivery tasks outlive
    the request whose connection FastAPI would otherwise close under them.
    """
    settings = get_settings()
    return ConversationDeps(
        settings=settings,
        conn_factory=lambda: db.connect(settings.invite_db_path),
        run_manager=get_run_manager(),
        build_client=build_client_for_run,
        linq=get_linq_client(),
    )


def reset_caches_for_testing() -> None:
    """Test-only helper: clears the lru_cache'd singletons so a test process
    can point them at a fresh temp DB / settings between tests."""
    get_settings.cache_clear()
    get_run_manager.cache_clear()
    get_linq_client.cache_clear()
    get_conversation_deps.cache_clear()

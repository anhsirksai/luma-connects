from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, Header, HTTPException

from invite_finder import auth, db
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


def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> None:
    """Gate a route behind a passcode-issued session.

    When ADMIN_PHONE is unset the gate is open — there is no way to deliver a
    passcode, so enforcing it would lock the operator out of their own API.
    That is safe on localhost and dangerous on a public URL, which is why
    startup logs a warning and /api/health reports `admin_auth` as "off".
    """
    if not settings.admin_phone:
        return

    token = auth.bearer_from_header(authorization) or (x_admin_token or "").strip()
    if not auth.session_is_valid(conn, token):
        raise HTTPException(
            status_code=401,
            detail="Admin session required. POST /api/auth/request-code to get a passcode.",
        )


def reset_caches_for_testing() -> None:
    """Test-only helper: clears the lru_cache'd singletons so a test process
    can point them at a fresh temp DB / settings between tests."""
    get_settings.cache_clear()
    get_run_manager.cache_clear()
    get_linq_client.cache_clear()
    get_conversation_deps.cache_clear()

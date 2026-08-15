"""Operator login: request a passcode by text, exchange it for a session.

These three routes are deliberately NOT behind the admin gate — they are how
you get through it. Everything else that serves person data is.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from invite_finder import auth
from invite_finder.api.deps import get_conn, get_linq_client, get_settings
from invite_finder.config import Settings
from invite_finder.linq import LinqClient, LinqError
from invite_finder.store import auth_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RequestCodeResponse(BaseModel):
    sent: bool
    detail: str


class VerifyRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class VerifyResponse(BaseModel):
    token: str
    expires_in_hours: int


@router.post("/request-code", response_model=RequestCodeResponse)
def request_code(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
    linq: LinqClient = Depends(get_linq_client),
) -> RequestCodeResponse:
    if not settings.admin_phone:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_PHONE is not configured, so the API is running unguarded.",
        )

    # Check deliverability before minting. A code nobody can receive is worse
    # than no code: it invalidates the previous one and spends the rate limit.
    if not linq.enabled:
        raise HTTPException(
            status_code=503,
            detail="LINQ_API_KEY is not configured, so no passcode could be delivered.",
        )

    try:
        issued = auth.issue_passcode(conn)
    except auth.AuthError as exc:
        # 429: the caller is asking too often. The message is safe to show —
        # it reveals only that a code was recently sent.
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    try:
        linq.send_message([settings.admin_phone], auth.passcode_message(issued.code))
    except Exception as exc:  # noqa: BLE001 - rollback must be unconditional
        # Deliberately broad. A LinqError is only one way this fails; a refused
        # connection, DNS failure or timeout raises requests' own exceptions,
        # and those must roll the code back too. Letting one escape would 500
        # *and* leave an undelivered code holding the rate-limit window —
        # locking the operator out over a text that never arrived.
        auth_store.delete_passcode(conn, issued.passcode_id)
        raise HTTPException(
            status_code=502, detail=f"Could not send the passcode: {exc}"
        ) from exc

    return RequestCodeResponse(
        sent=True, detail=f"Passcode sent to the number ending {settings.admin_phone[-4:]}."
    )


@router.post("/verify", response_model=VerifyResponse)
def verify(
    payload: VerifyRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> VerifyResponse:
    if not auth.verify_passcode(conn, payload.code):
        # One message for wrong, expired, exhausted and never-issued alike.
        raise HTTPException(status_code=401, detail="That passcode is not valid.")

    token = auth.start_session(conn, settings, label="passcode")
    return VerifyResponse(token=token, expires_in_hours=settings.admin_session_ttl_hours)


@router.post("/logout")
def logout(
    authorization: str | None = Header(default=None),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, bool]:
    token = auth.bearer_from_header(authorization)
    return {"revoked": auth.end_session(conn, token)}

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from invite_finder.api.deps import build_client_for_run, get_conn, get_run_manager, get_settings
from invite_finder.api.schemas import (
    CreateEventRequest,
    CreateEventResponse,
    EventDetail,
    EventListResponse,
    PeopleListResponse,
)
from invite_finder.api.serialize import (
    build_event_summary,
    build_run_status,
    event_ingest_warnings,
    row_to_person_summary,
)
from invite_finder.config import Settings
from invite_finder.luma import parse_luma_slug
from invite_finder.pipeline import compute_snapshot_for_event
from invite_finder.runner import RunManager
from invite_finder.store import event_store, people_store, run_store

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_model=EventListResponse)
def list_events(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
) -> EventListResponse:
    rows, total = event_store.list_events(
        conn, start_from=from_, start_to=to, limit=limit, offset=offset
    )
    return EventListResponse(
        events=[build_event_summary(conn, row) for row in rows], total=total
    )


@router.post("/events", response_model=CreateEventResponse)
async def create_event(
    payload: CreateEventRequest,
    response: Response,
    conn: sqlite3.Connection = Depends(get_conn),
    run_manager: RunManager = Depends(get_run_manager),
    settings: Settings = Depends(get_settings),
) -> CreateEventResponse:
    slug = parse_luma_slug(payload.luma_url)  # raises LumaUrlError -> 400 (global handler)

    active = run_store.latest_active_run_for_slug(conn, slug)
    if active is not None:
        raise HTTPException(
            status_code=409, detail=f"A run is already in progress for '{slug}'."
        )

    existing_event = event_store.get_by_slug(conn, slug)
    if existing_event is not None and not payload.force_refresh:
        response.status_code = 200
        return CreateEventResponse(
            event_id=existing_event["id"], status="ready", already_cached=True
        )

    if not settings.invite_offline and not (
        settings.brightdata_api_key
        and settings.brightdata_serp_zone
        and settings.brightdata_unlocker_zone
    ):
        raise HTTPException(
            status_code=503,
            detail="Bright Data is not configured (missing API key or zones).",
        )

    run_id = run_store.create_run(
        conn,
        event_id=existing_event["id"] if existing_event else None,
        input_url=payload.luma_url,
        params={"max_profiles": payload.max_profiles, "force_refresh": payload.force_refresh},
    )
    run_manager.start(
        run_id,
        luma_url=payload.luma_url,
        build_client=build_client_for_run,
        agent_model=settings.openai_agent_model,
        max_profiles=payload.max_profiles,
    )
    response.status_code = 202
    return CreateEventResponse(
        run_id=run_id,
        event_id=existing_event["id"] if existing_event else None,
        status="queued",
    )


@router.get("/events/{event_id}", response_model=EventDetail)
def get_event(event_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> EventDetail:
    event_row = event_store.get_by_id(conn, event_id)
    if event_row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    summary = build_event_summary(conn, event_row)
    snapshot = compute_snapshot_for_event(conn, event_id)
    counts = people_store.count_event_people(conn, event_id)
    run = run_store.latest_run_for_event(conn, event_id)

    return EventDetail(
        event=summary,
        snapshot=snapshot,
        counts=counts,
        ingest_source=event_row["ingest_source"],
        ingest_warnings=event_ingest_warnings(event_row),
        last_run=build_run_status(conn, run) if run is not None else None,
    )


@router.get("/events/{event_id}/people", response_model=PeopleListResponse)
def list_people(
    event_id: int,
    field: str | None = Query(default=None),
    role_type: str | None = Query(default=None),
    seniority: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    confirmed: bool | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
) -> PeopleListResponse:
    event_row = event_store.get_by_id(conn, event_id)
    if event_row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    rows, total = people_store.list_event_people(
        conn,
        event_id,
        field=field,
        role_type=role_type,
        seniority=seniority,
        industry=industry,
        confirmed=confirmed,
        q=q,
        limit=limit,
        offset=offset,
    )
    return PeopleListResponse(people=[row_to_person_summary(r) for r in rows], total=total)


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    event_row = event_store.get_by_id(conn, event_id)
    if event_row is None:
        raise HTTPException(status_code=404, detail="Event not found.")
    event_store.delete_event(conn, event_id)
    return Response(status_code=204)

from __future__ import annotations

import json
import sqlite3

from invite_finder.api.schemas import (
    ChatMessageOut,
    EventSummary,
    PersonSummary,
    RunEventOut,
    RunStatusOut,
)
from invite_finder.chat import PersonLabelsView
from invite_finder.pipeline import compute_snapshot_for_event
from invite_finder.snapshot import top_field_bars
from invite_finder.store import event_store, people_store, run_store


def build_event_summary(conn: sqlite3.Connection, event_row: sqlite3.Row) -> EventSummary:
    event_id = event_row["id"]
    counts = people_store.count_event_people(conn, event_id)
    snapshot = compute_snapshot_for_event(conn, event_id)
    top_fields = top_field_bars(snapshot, limit=3)

    run = run_store.latest_run_for_event(conn, event_id)
    if run and run["status"] in ("queued", "running"):
        status = "running"
    elif counts["total"] > 0:
        status = "ready"
    elif run and run["status"] == "failed":
        status = "failed"
    else:
        status = "pending"

    return EventSummary(
        id=event_id,
        slug=event_row["luma_slug"],
        name=event_row["name"],
        cover_url=event_row["cover_url"],
        start_at=event_row["start_at"],
        end_at=event_row["end_at"],
        timezone=event_row["timezone"],
        venue_name=event_row["venue_name"],
        city=event_row["city"],
        guest_count=event_row["guest_count"],
        people_analyzed=counts["total"],
        top_fields=top_fields,
        status=status,
    )


def build_run_status(conn: sqlite3.Connection, run_row: sqlite3.Row, limit: int = 50) -> RunStatusOut:
    events = run_store.recent_events(conn, run_row["id"], limit=limit)
    return RunStatusOut(
        id=run_row["id"],
        event_id=run_row["event_id"],
        status=run_row["status"],
        phase=run_row["phase"],
        stats=json.loads(run_row["stats_json"] or "{}"),
        error=run_row["error"],
        events=[
            RunEventOut(
                seq=e["seq"], ts=e["ts"], type=e["type"], message=e["message"],
                data=json.loads(e["data_json"] or "{}"),
            )
            for e in events
        ],
    )


def row_to_person_summary(row: sqlite3.Row) -> PersonSummary:
    return PersonSummary(
        person_id=row["id"],
        name=row["name"],
        headline=row["headline"],
        company=row["company"],
        linkedin_url=row["linkedin_url"],
        avatar_url=row["avatar_url"],
        is_confirmed_attendee=bool(row["is_confirmed"]),
        relation=row["relation"],
        labels=PersonLabelsView(
            field=row["field"],
            role_type=row["role_type"],
            seniority=row["seniority"],
            industries=json.loads(row["pc_industries_json"] or "[]"),
            tags=json.loads(row["tags_json"] or "[]"),
        ),
        relevance_score=row["relevance_score"],
    )


def row_to_chat_message(row: sqlite3.Row) -> ChatMessageOut:
    return ChatMessageOut(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        filters=json.loads(row["filters_json"]) if row["filters_json"] else None,
        cards=json.loads(row["cards_json"]) if row["cards_json"] else None,
        created_at=row["created_at"],
    )


def event_ingest_warnings(event_row: sqlite3.Row) -> list[str]:
    return event_store.warnings(event_row)

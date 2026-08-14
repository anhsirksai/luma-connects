from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from invite_finder.chat import PersonCard, PersonLabelsView
from invite_finder.snapshot import RoomSnapshot, SnapshotBar


class CreateEventRequest(BaseModel):
    luma_url: str
    force_refresh: bool = False
    max_profiles: int = Field(default=20, ge=1, le=200)


class CreateEventResponse(BaseModel):
    run_id: int | None = None
    event_id: int | None = None
    status: str
    already_cached: bool = False


class EventSummary(BaseModel):
    id: int
    slug: str
    name: str
    cover_url: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    timezone: str | None = None
    venue_name: str | None = None
    city: str | None = None
    guest_count: int | None = None
    people_analyzed: int
    top_fields: list[SnapshotBar]
    status: str


class EventListResponse(BaseModel):
    events: list[EventSummary]
    total: int


class RunEventOut(BaseModel):
    seq: int
    ts: str
    type: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class RunStatusOut(BaseModel):
    id: int
    event_id: int | None
    status: str
    phase: str | None
    stats: dict[str, Any]
    error: str | None
    events: list[RunEventOut]


class EventDetail(BaseModel):
    event: EventSummary
    snapshot: RoomSnapshot
    counts: dict[str, int]
    ingest_source: str | None
    ingest_warnings: list[str]
    last_run: RunStatusOut | None


class PersonSummary(BaseModel):
    person_id: int
    name: str | None
    headline: str | None
    company: str | None
    linkedin_url: str | None
    avatar_url: str | None
    is_confirmed_attendee: bool
    relation: str
    labels: PersonLabelsView
    relevance_score: int | None


class PeopleListResponse(BaseModel):
    people: list[PersonSummary]
    total: int


class ChatRequest(BaseModel):
    message: str
    thread_id: int | None = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    filters: dict[str, Any] | None = None
    cards: list[dict[str, Any]] | None = None
    created_at: str


class ChatMessagesResponse(BaseModel):
    messages: list[ChatMessageOut]


class HealthResponse(BaseModel):
    status: str
    db: str
    offline: bool


__all__ = [
    "ChatMessageOut",
    "ChatMessagesResponse",
    "ChatRequest",
    "CreateEventRequest",
    "CreateEventResponse",
    "EventDetail",
    "EventListResponse",
    "EventSummary",
    "HealthResponse",
    "PeopleListResponse",
    "PersonCard",
    "PersonSummary",
    "RunEventOut",
    "RunStatusOut",
]

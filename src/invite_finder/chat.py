from __future__ import annotations

import json
import sqlite3
from typing import Any

from agents import Agent, Runner
from pydantic import BaseModel, Field

from invite_finder.store import chat_store, people_store
from invite_finder.taxonomy import (
    FieldCategory,
    Industry,
    RoleType,
    Seniority,
    field_values,
    industry_values,
    role_type_values,
    seniority_values,
)

FALLBACK_THRESHOLD = 3
DEFAULT_LIMIT = 8
MAX_LIMIT = 20
ROSTER_FALLBACK_LIMIT = 300


class PersonFilter(BaseModel):
    fields: list[FieldCategory] = Field(default_factory=list)
    role_types: list[RoleType] = Field(default_factory=list)
    seniorities: list[Seniority] = Field(default_factory=list)
    industries: list[Industry] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    company_keywords: list[str] = Field(default_factory=list)
    headline_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    confirmed_only: bool = False
    limit: int = DEFAULT_LIMIT
    interpretation: str = ""


class PersonLabelsView(BaseModel):
    field: str | None = None
    role_type: str | None = None
    seniority: str | None = None
    industries: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class PersonCard(BaseModel):
    person_id: int
    name: str | None = None
    headline: str | None = None
    company: str | None = None
    linkedin_url: str | None = None
    avatar_url: str | None = None
    highlight: str
    why_relevant: str
    is_confirmed_attendee: bool
    relation: str
    labels: PersonLabelsView
    relevance_score: int | None = None
    evidence: list[str] = Field(default_factory=list)


class ChatQueryResponse(BaseModel):
    thread_id: int
    message_id: int
    reply: str
    interpreted_filters: PersonFilter
    cards: list[PersonCard]
    total_matches: int
    used_fallback: bool
    caveats: list[str] = Field(default_factory=list)


class _FallbackPick(BaseModel):
    person_ids: list[int]
    reason: str


class _HighlightItem(BaseModel):
    person_id: int
    highlight: str
    why_relevant: str


class _HighlightBatch(BaseModel):
    reply: str
    items: list[_HighlightItem]


def build_interpret_agent(model: str) -> Agent:
    return Agent(
        name="Event chat query interpreter",
        model=model,
        output_type=PersonFilter,
        instructions=(
            "You translate a natural-language question about event attendees "
            "into a structured filter over a fixed taxonomy. Only use field, "
            "role_type, seniority, and industry values from the lists you are "
            "given -- never invent new enum values. Free-text tags_any and "
            "company_keywords/headline_keywords may be used for anything not "
            "covered by the enums (e.g. a specific company or niche). Set "
            "interpretation to one short phrase describing what you understood "
            "the user to be asking for. Keep limit between 1 and 20, default 8."
        ),
    )


def build_fallback_agent(model: str) -> Agent:
    return Agent(
        name="Event chat roster fallback",
        model=model,
        output_type=_FallbackPick,
        instructions=(
            "You are given a compact roster of event attendees and a user "
            "question that a structured filter failed to match well. Pick the "
            "person_ids (integers) who best answer the question, in order of "
            "relevance. Only use ids that appear in the roster. If nothing "
            "fits, return an empty list rather than guessing."
        ),
    )


def build_highlight_agent(model: str) -> Agent:
    return Agent(
        name="Event chat highlight writer",
        model=model,
        output_type=_HighlightBatch,
        instructions=(
            "You write short, factual highlights for a shortlist of event "
            "attendees, in response to a user's question. For each person "
            "write a 1-2 sentence highlight and a one-clause why_relevant tied "
            "to the actual question. Use only the fields you were given -- "
            "never invent an employer, title, or accomplishment. If the "
            "evidence is thin, say so plainly instead of padding. Also write "
            "one short reply sentence framing the results as a whole (do not "
            "list the people by name in the reply; the UI renders cards for "
            "that)."
        ),
    )


async def interpret_query(
    message: str, *, model: str, available_tags: list[str]
) -> PersonFilter:
    """The LLM seam for turning free text into a structured filter. Tests
    stub this function directly instead of mocking the OpenAI SDK."""
    agent = build_interpret_agent(model)
    prompt = (
        f"User question: {message!r}\n\n"
        f"Valid field values: {', '.join(field_values())}\n"
        f"Valid role_type values: {', '.join(role_type_values())}\n"
        f"Valid seniority values: {', '.join(seniority_values())}\n"
        f"Valid industry values: {', '.join(industry_values())}\n"
        f"Tags actually present at this event: {', '.join(available_tags) or '(none yet)'}\n"
    )
    result = await Runner.run(agent, prompt, max_turns=4)
    return result.final_output


async def fallback_pick(
    message: str, *, model: str, roster: list[dict[str, Any]]
) -> _FallbackPick:
    """The LLM seam for the low-recall fallback path. Tests stub this too."""
    agent = build_fallback_agent(model)
    roster_lines = "\n".join(
        f"- id={p['id']} | {p.get('name') or 'Unknown'} | {p.get('headline') or ''} | "
        f"{p.get('company') or ''} | field={p.get('field')} role={p.get('role_type')} "
        f"seniority={p.get('seniority')} industries={p.get('industries')}"
        for p in roster
    )
    prompt = f"User question: {message!r}\n\nRoster:\n{roster_lines}"
    result = await Runner.run(agent, prompt, max_turns=4)
    return result.final_output


async def write_highlights(
    message: str, *, model: str, shortlist: list[dict[str, Any]]
) -> _HighlightBatch:
    """The LLM seam for writing per-person highlights. Tests stub this too."""
    agent = build_highlight_agent(model)
    shortlist_lines = "\n".join(
        f"- id={p['id']} | {p.get('name') or 'Unknown'} | {p.get('headline') or ''} | "
        f"{p.get('company') or ''} | evidence={p.get('evidence')}"
        for p in shortlist
    )
    prompt = f"User question: {message!r}\n\nShortlist:\n{shortlist_lines}"
    result = await Runner.run(agent, prompt, max_turns=4)
    return result.final_output


def _row_to_roster_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "headline": row["headline"],
        "company": row["company"],
        "field": row["field"],
        "role_type": row["role_type"],
        "seniority": row["seniority"],
        "industries": json.loads(row["pc_industries_json"] or "[]"),
        "evidence": json.loads(row["evidence_json"] or "[]"),
    }


def _row_to_card(
    row: sqlite3.Row, *, highlight: str, why_relevant: str
) -> PersonCard:
    return PersonCard(
        person_id=row["id"],
        name=row["name"],
        headline=row["headline"],
        company=row["company"],
        linkedin_url=row["linkedin_url"],
        avatar_url=row["avatar_url"],
        highlight=highlight,
        why_relevant=why_relevant,
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
        evidence=json.loads(row["evidence_json"] or "[]"),
    )


async def answer_chat_query(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    message: str,
    thread_id: int | None,
    model: str,
) -> ChatQueryResponse:
    if thread_id is None:
        thread_id = chat_store.create_thread(conn, event_id, title=message[:80])
    chat_store.add_message(conn, thread_id, role="user", content=message)

    available_tags = people_store.top_tags(conn, event_id)
    person_filter = await interpret_query(message, model=model, available_tags=available_tags)
    person_filter.limit = max(1, min(person_filter.limit or DEFAULT_LIMIT, MAX_LIMIT))

    matches, total = _query_with_filter(conn, event_id, person_filter)
    used_fallback = False
    caveats: list[str] = []

    if len(matches) < FALLBACK_THRESHOLD:
        used_fallback = True
        roster_rows, _ = people_store.list_event_people(
            conn, event_id, limit=ROSTER_FALLBACK_LIMIT
        )
        roster = [_row_to_roster_dict(r) for r in roster_rows]
        try:
            pick = await fallback_pick(message, model=model, roster=roster)
            by_id = {r["id"]: r for r in roster_rows}
            matches = [by_id[pid] for pid in pick.person_ids if pid in by_id][:MAX_LIMIT]
            total = len(matches)
            if not matches:
                caveats.append(
                    "No attendees clearly matched this question from the "
                    "profiles available for this event."
                )
        except Exception as exc:  # noqa: BLE001
            caveats.append(f"Fallback search could not run: {exc}")

    shortlist = [
        {
            "id": row["id"],
            "name": row["name"],
            "headline": row["headline"],
            "company": row["company"],
            "evidence": json.loads(row["evidence_json"] or "[]"),
        }
        for row in matches
    ]

    reply = "Here's what I found."
    highlights_by_id: dict[int, tuple[str, str]] = {}
    if shortlist:
        try:
            batch = await write_highlights(message, model=model, shortlist=shortlist)
            reply = batch.reply or reply
            highlights_by_id = {
                item.person_id: (item.highlight, item.why_relevant) for item in batch.items
            }
        except Exception as exc:  # noqa: BLE001
            caveats.append(f"Could not generate highlights: {exc}")

    cards = []
    for row in matches:
        highlight, why_relevant = highlights_by_id.get(
            row["id"],
            (
                row["headline"] or "No headline available from public sources.",
                person_filter.interpretation or "Matched your query.",
            ),
        )
        cards.append(_row_to_card(row, highlight=highlight, why_relevant=why_relevant))

    message_id = chat_store.add_message(
        conn,
        thread_id,
        role="assistant",
        content=reply,
        filters=person_filter.model_dump(),
        cards=[c.model_dump() for c in cards],
    )

    return ChatQueryResponse(
        thread_id=thread_id,
        message_id=message_id,
        reply=reply,
        interpreted_filters=person_filter,
        cards=cards,
        total_matches=total,
        used_fallback=used_fallback,
        caveats=caveats,
    )


def _query_with_filter(
    conn: sqlite3.Connection, event_id: int, person_filter: PersonFilter
) -> tuple[list[sqlite3.Row], int]:
    """Deterministic SQL filter/rank -- the primary (non-fallback) path."""
    candidates: list[sqlite3.Row] = []
    seen_ids: set[int] = set()

    def _collect(**kwargs: Any) -> None:
        rows, _ = people_store.list_event_people(
            conn, event_id, limit=MAX_LIMIT * 3, confirmed=(True if person_filter.confirmed_only else None), **kwargs
        )
        for row in rows:
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                candidates.append(row)

    if person_filter.fields:
        for field in person_filter.fields:
            _collect(field=field.value)
    if person_filter.role_types:
        for role_type in person_filter.role_types:
            _collect(role_type=role_type.value)
    if person_filter.seniorities:
        for seniority in person_filter.seniorities:
            _collect(seniority=seniority.value)
    if person_filter.industries:
        for industry in person_filter.industries:
            _collect(industry=industry.value)
    if person_filter.tags_any:
        rows, _ = people_store.list_event_people(conn, event_id, limit=MAX_LIMIT * 3)
        for row in rows:
            tags = json.loads(row["tags_json"] or "[]")
            if any(t in tags for t in person_filter.tags_any) and row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                candidates.append(row)
    for keyword in person_filter.company_keywords + person_filter.headline_keywords:
        _collect(q=keyword)

    if not candidates and not any(
        [
            person_filter.fields, person_filter.role_types, person_filter.seniorities,
            person_filter.industries, person_filter.tags_any,
            person_filter.company_keywords, person_filter.headline_keywords,
        ]
    ):
        # No filters were confidently derived; fall through empty so the
        # caller's low-recall fallback path takes over.
        return [], 0

    exclude = [e.lower() for e in person_filter.exclude_keywords]
    if exclude:
        def _excluded(row: sqlite3.Row) -> bool:
            haystack = " ".join(
                filter(None, [row["headline"], row["company"], row["name"]])
            ).lower()
            return any(e in haystack for e in exclude)

        candidates = [r for r in candidates if not _excluded(r)]

    candidates.sort(
        key=lambda r: (
            -(r["is_confirmed"] or 0),
            -(r["relevance_score"] or 0),
            -(r["pc_confidence"] or 0),
        )
    )
    total = len(candidates)
    return candidates[: person_filter.limit], total

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from agents import Runner

from invite_finder.agent import LeadFinderContext, build_profile_finder_agent, build_seed_search_queries_for_event
from invite_finder.classify import classify_event_people
from invite_finder.luma import fetch_luma_event
from invite_finder.luma_models import LumaEvent
from invite_finder.observability import NullReporter, RunReporter
from invite_finder.protocols import WebDataClient
from invite_finder.snapshot import RoomSnapshot, build_room_snapshot
from invite_finder.store import event_store, people_store, run_store

RunPhase = str  # queued|luma_fetch|seeding|serp_discovery|profile_enrichment|classification|snapshot|done|failed


def _luma_event_to_columns(event: LumaEvent) -> dict[str, Any]:
    return dict(
        luma_slug=event.slug,
        luma_api_id=event.luma_api_id,
        source_url=event.source_url,
        name=event.name,
        description=event.description,
        cover_url=event.cover_url,
        start_at=event.start_at.isoformat() if event.start_at else None,
        end_at=event.end_at.isoformat() if event.end_at else None,
        timezone=event.timezone,
        location_type=event.location_type,
        venue_name=event.venue.name,
        address=event.venue.address,
        city=event.venue.city,
        region=event.venue.region,
        country=event.venue.country,
        latitude=event.venue.latitude,
        longitude=event.venue.longitude,
        guest_count=event.guest_count,
        show_guest_list=int(event.show_guest_list),
        categories_json=json.dumps(event.categories),
        ingest_source=event.ingest_source,
        ingest_warnings_json=json.dumps(event.warnings),
        raw_json=json.dumps(event.raw) if event.raw is not None else None,
    )


def _seed_confirmed_people(
    conn: sqlite3.Connection, event_id: int, run_id: int, luma_event: LumaEvent
) -> int:
    count = 0
    for person in luma_event.people:
        person_id = people_store.upsert_person(
            conn,
            linkedin_url=person.linkedin_url,
            luma_user_api_id=person.luma_user_api_id,
            name=person.name,
            bio_short=person.bio_short,
            avatar_url=person.avatar_url,
        )
        people_store.link_person_to_event(
            conn,
            event_id=event_id,
            person_id=person_id,
            relation=person.relation,
            is_confirmed=True,
            run_id=run_id,
        )
        count += 1
    return count


def _default_serp_results_per_query(max_profiles: int, max_serp_queries: int) -> int:
    queries = max(1, max_serp_queries)
    needed_per_query = math.ceil(max_profiles / queries)
    return max(10, min(100, needed_per_query * 2))


async def _run_serp_discovery(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    run_id: int,
    luma_event: LumaEvent,
    client: WebDataClient,
    agent_model: str,
    city: str,
    max_profiles: int,
    max_serp_queries: int,
    max_page_fetches: int,
) -> dict[str, Any]:
    serp_results_per_query = _default_serp_results_per_query(max_profiles, max_serp_queries)
    max_agent_turns = max(20, max_serp_queries + max_page_fetches + 8)

    context = LeadFinderContext(
        brightdata=client,
        city=city,
        max_profiles=max_profiles,
        max_serp_queries=max_serp_queries,
        max_serp_results_per_query=serp_results_per_query,
        max_page_fetches=max_page_fetches,
    )
    agent = build_profile_finder_agent(agent_model)
    seed_queries = build_seed_search_queries_for_event(
        city=city,
        event_name=luma_event.name,
        categories=luma_event.categories,
        description=luma_event.description,
    )
    seed_query_text = "\n".join(f"- {q}" for q in seed_queries)

    prompt = (
        f'Find up to {max_profiles} public LinkedIn profiles in "{city}" who may '
        f"be interested in this event: {luma_event.source_url} ({luma_event.name}). "
        "Use Bright Data SERP and Web Unlocker tools. Start with these broad-to-"
        "specific SERP queries before inventing narrower exact-match queries:\n"
        f"{seed_query_text}\n"
        f"For each SERP query, request up to {serp_results_per_query} results. "
        "If the broad queries have no usable results, report that clearly "
        "instead of fabricating candidates. Return the structured report."
    )

    result = await Runner.run(agent, prompt, context=context, max_turns=max_agent_turns)
    report = result.final_output

    inferred_count = 0
    for candidate in report.candidates:
        person_id = people_store.upsert_person(
            conn,
            linkedin_url=candidate.linkedin_url,
            name=candidate.name,
            headline=candidate.headline,
            company=candidate.company,
        )
        people_store.link_person_to_event(
            conn,
            event_id=event_id,
            person_id=person_id,
            relation="inferred",
            is_confirmed=False,
            relevance_score=candidate.relevance_score,
            relevance_rationale=candidate.relevance_rationale,
            city_signal=candidate.city_signal,
            evidence=candidate.evidence,
            source_queries=candidate.source_queries,
            run_id=run_id,
        )
        inferred_count += 1

    return {
        "inferred_count": inferred_count,
        "serp_queries_used": len(context.serp_queries_used),
        "pages_fetched": len(context.fetched_pages),
        "caveats": report.caveats,
    }


def compute_snapshot_for_event(conn: sqlite3.Connection, event_id: int) -> RoomSnapshot:
    """Recomputed on demand from stored labels -- cheap (SQL only, no LLM), so
    this can be called on every GET without re-running the pipeline."""
    rows, _ = people_store.list_event_people(conn, event_id, limit=10_000)
    classifications = [
        {
            "field": row["field"],
            "role_type": row["role_type"],
            "seniority": row["seniority"],
            "industries": json.loads(row["pc_industries_json"] or "[]"),
        }
        for row in rows
        if row["field"] is not None
    ]
    counts = people_store.count_event_people(conn, event_id)
    event = event_store.get_by_id(conn, event_id)
    registered_count = event["guest_count"] if event else None

    return build_room_snapshot(
        classifications,
        registered_count=registered_count,
        confirmed_people=counts["confirmed"],
        inferred_people=counts["inferred"],
    )


async def run_event_pipeline(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    luma_url: str,
    client: WebDataClient,
    agent_model: str,
    reporter: RunReporter | None = None,
    max_profiles: int = 20,
    max_serp_queries: int = 4,
    max_page_fetches: int = 6,
    use_llm_classification: bool = True,
) -> int:
    """Runs the full pipeline for one Luma URL: fetch -> seed confirmed people
    -> SERP discovery -> classification -> snapshot. Returns the event id.

    All Bright Data access happens through `client`, which the caller builds
    (typically an ObservedBrightDataClient wrapping a BrightDataClient with a
    CachingSession) -- this function has no direct network dependency, which
    is what makes it testable offline against a stub or a seeded cache.
    """
    reporter = reporter or NullReporter()
    event_id: int | None = None

    def _phase(phase: str, message: str) -> None:
        run_store.update_status(conn, run_id, phase=phase)
        reporter.emit("phase", message, {"phase": phase})

    try:
        _phase("luma_fetch", f"Fetching event page: {luma_url}")
        luma_event = fetch_luma_event(client, luma_url)
        for warning in luma_event.warnings:
            reporter.emit("log", f"Ingestion note: {warning}")

        event_id = event_store.upsert_event(conn, **_luma_event_to_columns(luma_event))
        run_store.set_event_id(conn, run_id, event_id)
        reporter.emit(
            "log",
            f"Resolved event \"{luma_event.name}\" ({luma_event.guest_count or 'unknown'} registered)",
            {"event_id": event_id, "ingest_source": luma_event.ingest_source},
        )

        _phase("seeding", "Linking confirmed hosts and featured guests")
        confirmed_count = _seed_confirmed_people(conn, event_id, run_id, luma_event)
        reporter.emit("log", f"Linked {confirmed_count} confirmed people from Luma")

        city = luma_event.venue.city
        discovery_stats: dict[str, Any] = {"inferred_count": 0}
        if city:
            _phase("serp_discovery", f"Searching for likely attendees near {city}")
            discovery_stats = await _run_serp_discovery(
                conn,
                event_id=event_id,
                run_id=run_id,
                luma_event=luma_event,
                client=client,
                agent_model=agent_model,
                city=city,
                max_profiles=max_profiles,
                max_serp_queries=max_serp_queries,
                max_page_fetches=max_page_fetches,
            )
            reporter.emit(
                "log",
                f"Inferred {discovery_stats['inferred_count']} additional people "
                f"from {discovery_stats['serp_queries_used']} searches",
            )
            for caveat in discovery_stats.get("caveats") or []:
                reporter.emit("log", f"Discovery caveat: {caveat}")
        else:
            reporter.emit(
                "log",
                "No city could be resolved for this event; skipping SERP "
                "discovery and using only confirmed Luma guests.",
            )

        # Profile enrichment already happens inside SERP discovery: the
        # agent's own fetch_public_profile_page tool call is budgeted by
        # max_page_fetches, so a separate enrichment pass would just repeat
        # already-cached fetches. This phase exists for UI/progress clarity.
        _phase("profile_enrichment", "Reviewing public profile evidence")
        reporter.emit(
            "log", f"Used {discovery_stats.get('pages_fetched', 0)} profile page fetches"
        )

        _phase("classification", "Classifying attendees by field, role, and seniority")
        classified_count = await classify_event_people(
            conn, event_id, model=agent_model, use_llm=use_llm_classification, reporter=reporter
        )
        reporter.emit("log", f"Classified {classified_count} people")

        _phase("snapshot", "Building the room snapshot")
        snapshot = compute_snapshot_for_event(conn, event_id)
        run_store.update_status(
            conn,
            run_id,
            stats={
                "confirmed_count": confirmed_count,
                "inferred_count": discovery_stats.get("inferred_count", 0),
                "classified_count": classified_count,
                "classified_people": snapshot.basis.classified_people,
            },
        )

        run_store.update_status(conn, run_id, status="succeeded", phase="done", finished=True)
        reporter.emit("done", "Run complete", {"event_id": event_id})
        return event_id

    except Exception as exc:  # noqa: BLE001 - record failure, then re-raise
        run_store.update_status(
            conn, run_id, status="failed", error=str(exc), finished=True
        )
        reporter.emit("error", f"Run failed: {exc}")
        raise

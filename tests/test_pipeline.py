from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from invite_finder import db, pipeline
from invite_finder import classify as classify_module
from invite_finder.brightdata import BrightDataClient
from invite_finder.cache import CachingSession, seed_from_fixture
from invite_finder.classify import ClassificationBatch, PersonLabels
from invite_finder.config import Settings
from invite_finder.models import CandidateProfile, ProfileSearchReport
from invite_finder.observability import ObservedBrightDataClient
from invite_finder.runner import RunReporterImpl
from invite_finder.store import event_store, people_store, run_store
from invite_finder.taxonomy import FieldCategory, RoleType, Seniority

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "luma_api_vla_night_panel.json"


def make_settings() -> Settings:
    return Settings(
        brightdata_api_key="test-key",
        brightdata_serp_zone="zone-a",
        brightdata_unlocker_zone="zone-b",
    )


def seed_luma_api_cache(conn) -> None:
    fixture_text = FIXTURE_PATH.read_text()
    wrapper_body = json.dumps({"body": fixture_text})
    seed_from_fixture(
        conn,
        payload={
            "url": "https://api.lu.ma/url?url=vla-night-panel",
            "format": "raw",
            "method": "GET",
        },
        body=wrapper_body,
    )


@dataclass
class _FakeRunResult:
    final_output: Any


class _StubRunner:
    """Replaces agents.Runner in the pipeline module for offline tests -- no
    OpenAI call, no Bright Data call, just a canned discovery report."""

    @staticmethod
    async def run(agent, prompt, *, context=None, max_turns=None):
        report = ProfileSearchReport(
            event_url="https://luma.com/vla-night-panel",
            city="San Francisco",
            event_summary="A robotics and embodied AI panel.",
            audience_hypothesis="Robotics engineers, researchers, and investors.",
            search_queries_used=["stubbed query"],
            candidates=[
                CandidateProfile(
                    name="Alex Rivera",
                    linkedin_url="https://www.linkedin.com/in/alex-rivera-robotics",
                    headline="Founder & CEO at Nimbus Robotics",
                    company="Nimbus Robotics",
                    city_signal="San Francisco Bay Area",
                    relevance_score=88,
                    relevance_rationale="Founder building robotics foundation models.",
                    evidence=["SERP snippet"],
                    source_queries=["stubbed query"],
                ),
            ],
            caveats=[],
        )
        return _FakeRunResult(final_output=report)


async def _fake_classify_batch(people, *, model, hints=None):
    hints = hints or {}
    labels = []
    for person in people:
        hint = hints.get(person.person_ref)
        labels.append(
            PersonLabels(
                person_ref=person.person_ref,
                field=(hint.field if hint and hint.field else FieldCategory.OTHER),
                role_type=(hint.role_type if hint and hint.role_type else RoleType.WORKING_PROFESSIONAL),
                seniority=(hint.seniority if hint and hint.seniority else Seniority.UNKNOWN),
                industries=(hint.industries if hint else []),
                tags=(hint.tags if hint else []),
                confidence=0.6,
            )
        )
    return ClassificationBatch(labels=labels)


@pytest.fixture(autouse=True)
def _stub_llm_calls(monkeypatch):
    monkeypatch.setattr(pipeline, "Runner", _StubRunner)
    monkeypatch.setattr(classify_module, "classify_batch", _fake_classify_batch)


def make_offline_client(conn, reporter):
    session = CachingSession(conn, offline=True)
    inner = BrightDataClient(make_settings(), session=session)
    return ObservedBrightDataClient(inner, reporter)


async def _run_pipeline(conn) -> int:
    run_id = run_store.create_run(conn, event_id=None, input_url="https://luma.com/vla-night-panel")
    reporter = RunReporterImpl(conn, run_id)
    client = make_offline_client(conn, reporter)
    event_id = await pipeline.run_event_pipeline(
        conn,
        run_id,
        luma_url="https://luma.com/vla-night-panel",
        client=client,
        agent_model="gpt-5.5",
        reporter=reporter,
        max_profiles=10,
        max_serp_queries=2,
        max_page_fetches=2,
        use_llm_classification=True,
    )
    return run_id, event_id


@pytest.mark.asyncio
async def test_full_pipeline_offline_produces_event_people_and_snapshot() -> None:
    conn = db.connect(":memory:")
    seed_luma_api_cache(conn)

    run_id, event_id = await _run_pipeline(conn)

    event = event_store.get_by_id(conn, event_id)
    assert event["luma_slug"] == "vla-night-panel"
    assert event["guest_count"] == 552

    counts = people_store.count_event_people(conn, event_id)
    assert counts["confirmed"] == 12  # 2 hosts + 10 featured guests
    assert counts["inferred"] == 1  # 1 stubbed SERP candidate
    assert counts["total"] == 13

    assert people_store.count_classified(conn, event_id) == 13

    run = run_store.get_run(conn, run_id)
    assert run["status"] == "succeeded"
    assert run["phase"] == "done"

    events = run_store.list_events_after(conn, run_id, after_seq=0)
    assert len(events) > 0
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert events[-1]["type"] == "done"

    snapshot = pipeline.compute_snapshot_for_event(conn, event_id)
    assert snapshot.basis.classified_people == 13
    assert snapshot.basis.confirmed_people == 12
    assert snapshot.basis.registered_count == 552


@pytest.mark.asyncio
async def test_pipeline_is_free_on_second_run_against_same_cache() -> None:
    """The hard persistence requirement: re-running against a warm cache makes
    zero additional Bright Data (network) calls."""
    conn = db.connect(":memory:")
    seed_luma_api_cache(conn)

    await _run_pipeline(conn)

    # A fresh offline client with an empty in-memory 'network' -- if the second
    # run tried to hit the network it would raise CacheMiss immediately, since
    # this is offline=True and nothing new was seeded.
    _, event_id_2 = await _run_pipeline(conn)

    events, total = event_store.list_events(conn)
    assert total == 1  # same event, upserted in place, not duplicated

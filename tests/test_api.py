from __future__ import annotations

import json
import os
import tempfile

_TMP_DB_DIR = tempfile.mkdtemp(prefix="invite_finder_test_")
os.environ["INVITE_DB_PATH"] = os.path.join(_TMP_DB_DIR, "test.db")
os.environ["INVITE_OFFLINE"] = "1"
os.environ.setdefault("BRIGHTDATA_API_KEY", "test-key")
os.environ.setdefault("BRIGHTDATA_SERP_ZONE", "test-zone")
os.environ.setdefault("BRIGHTDATA_UNLOCKER_ZONE", "test-zone")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from invite_finder import db  # noqa: E402
from invite_finder import chat as chat_module  # noqa: E402
from invite_finder.api import deps  # noqa: E402
from invite_finder.api.app import app  # noqa: E402
from invite_finder.chat import PersonFilter, _HighlightBatch, _HighlightItem  # noqa: E402
from invite_finder.runner import RunManager, RunReporterImpl  # noqa: E402
from invite_finder.store import event_store, people_store, run_store  # noqa: E402
from invite_finder.taxonomy import Industry  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(app)


def get_conn():
    return db.connect(deps.get_settings().invite_db_path)


def make_event(slug: str, *, name: str = "Test Event", guest_count: int = 100) -> int:
    conn = get_conn()
    try:
        event_id = event_store.upsert_event(
            conn,
            luma_slug=slug,
            luma_api_id=f"evt-{slug}",
            source_url=f"https://luma.com/{slug}",
            name=name,
            description="A test event.",
            cover_url=None,
            start_at="2026-08-13T16:30:00.000Z",
            end_at="2026-08-14T01:30:00.000Z",
            timezone="America/Los_Angeles",
            location_type="offline",
            venue_name="Test Venue",
            address="1 Test St",
            city="San Francisco",
            region="California",
            country="United States",
            latitude=37.0,
            longitude=-122.0,
            guest_count=guest_count,
            show_guest_list=1,
            categories_json="[]",
            ingest_source="luma_api",
            ingest_warnings_json="[]",
            raw_json="{}",
        )
        return event_id
    finally:
        conn.close()


def add_person(
    conn, event_id, *, name, headline=None, company=None, confirmed=True,
    field="software_engineering", role_type="working_professional",
    seniority="mid", industries=None,
):
    person_id = people_store.upsert_person(
        conn,
        linkedin_url=f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}",
        name=name, headline=headline, company=company,
    )
    people_store.link_person_to_event(
        conn, event_id=event_id, person_id=person_id,
        relation="host" if confirmed else "inferred",
        is_confirmed=confirmed, relevance_score=None if confirmed else 75,
    )
    people_store.upsert_classification(
        conn, person_id=person_id, input_fingerprint=f"fp-{person_id}", taxonomy_version=1,
        field=field, field_other_label=None, role_type=role_type, seniority=seniority,
        industries=industries or [], tags=[], confidence=0.7, method="rules", model=None,
    )
    return person_id


def test_health(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["offline"] is True


def test_list_events_empty_initially_is_fine(client) -> None:
    response = client.get("/api/events?limit=1&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert "events" in body and "total" in body


def test_create_event_rejects_non_luma_url(client) -> None:
    response = client.post("/api/events", json={"luma_url": "https://example.com/not-luma"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_luma_url"


def test_create_event_then_conflict_then_cached(client, monkeypatch) -> None:
    # Stub out real background execution -- the pipeline itself is already
    # covered end-to-end in test_pipeline.py. Here we're testing the route's
    # own orchestration: create/conflict/cached-response logic.
    monkeypatch.setattr(RunManager, "start", lambda self, run_id, **kwargs: None)

    first = client.post("/api/events", json={"luma_url": "https://luma.com/api-test-event"})
    assert first.status_code == 202
    body = first.json()
    assert body["status"] == "queued"
    assert body["run_id"] is not None

    second = client.post("/api/events", json={"luma_url": "https://luma.com/api-test-event"})
    assert second.status_code == 409

    # Simulate the run finishing (as the real pipeline would) by creating the
    # event row directly, then a fresh request should report it as cached.
    make_event("api-test-event", name="API Test Event")
    conn = get_conn()
    try:
        run = run_store.latest_active_run_for_slug(conn, "api-test-event")
        if run is not None:
            run_store.update_status(conn, run["id"], status="succeeded", finished=True)
    finally:
        conn.close()

    third = client.post("/api/events", json={"luma_url": "https://luma.com/api-test-event"})
    assert third.status_code == 200
    assert third.json()["already_cached"] is True


def test_create_event_force_refresh_reaches_the_caching_session(client, monkeypatch) -> None:
    """The API's force_refresh flag must actually reach CachingSession, not
    just get stored as run metadata -- otherwise 'force refresh' silently
    replays stale cached Bright Data responses forever."""
    make_event("force-refresh-event", name="Force Refresh Event")

    captured: dict = {}

    def fake_start(self, run_id, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(RunManager, "start", fake_start)

    response = client.post(
        "/api/events",
        json={"luma_url": "https://luma.com/force-refresh-event", "force_refresh": True},
    )
    assert response.status_code == 202

    conn = get_conn()
    try:
        client_instance = captured["build_client"](conn, RunReporterImpl(conn, 1))
    finally:
        conn.close()
    assert client_instance.inner.session.force_refresh is True


def test_get_event_detail_and_people(client) -> None:
    event_id = make_event("detail-test-event", name="Detail Test Event", guest_count=250)
    conn = get_conn()
    try:
        add_person(conn, event_id, name="Host Person", confirmed=True, field="business_operations")
        add_person(
            conn, event_id, name="Investor Person", confirmed=False,
            headline="Partner at Test Ventures", field="finance_investing",
            industries=[Industry.VC_INVESTOR.value],
        )
    finally:
        conn.close()

    detail = client.get(f"/api/events/{event_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["event"]["slug"] == "detail-test-event"
    assert body["event"]["people_analyzed"] == 2
    assert body["counts"]["confirmed"] == 1
    assert body["counts"]["inferred"] == 1
    assert body["snapshot"]["basis"]["classified_people"] == 2

    people = client.get(f"/api/events/{event_id}/people?industry=vc_investor")
    assert people.status_code == 200
    people_body = people.json()
    assert people_body["total"] == 1
    assert people_body["people"][0]["name"] == "Investor Person"


def test_get_event_404_for_missing_event(client) -> None:
    response = client.get("/api/events/999999")
    assert response.status_code == 404


def test_run_status_and_stream(client) -> None:
    event_id = make_event("run-stream-event")
    conn = get_conn()
    try:
        run_id = run_store.create_run(conn, event_id=event_id, input_url="https://luma.com/run-stream-event")
        run_store.update_status(conn, run_id, status="running", phase="luma_fetch", started=True)
        run_store.append_event(conn, run_id, type_="phase", message="Fetching event")
        run_store.append_event(conn, run_id, type_="log", message="Linked 2 people")
        run_store.update_status(conn, run_id, status="succeeded", phase="done", finished=True)
    finally:
        conn.close()

    status_response = client.get(f"/api/runs/{run_id}")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "succeeded"
    assert len(status_body["events"]) == 2

    with client.stream("GET", f"/api/runs/{run_id}/stream") as stream_response:
        assert stream_response.status_code == 200
        collected = []
        for line in stream_response.iter_lines():
            if line.startswith("data:"):
                collected.append(json.loads(line[len("data:"):].strip()))
            if len(collected) >= 2:
                break
    assert len(collected) == 2
    assert collected[0]["message"] == "Fetching event"


def test_run_status_404_for_missing_run(client) -> None:
    assert client.get("/api/runs/999999").status_code == 404
    assert client.get("/api/runs/999999/stream").status_code == 404


async def _stub_interpret_vc(message, *, model, available_tags):
    return PersonFilter(industries=[Industry.VC_INVESTOR], interpretation="Looking for investors")


async def _stub_write_highlights(message, *, model, shortlist):
    items = [
        _HighlightItem(person_id=p["id"], highlight=f"Highlight for {p['name']}", why_relevant="VC match")
        for p in shortlist
    ]
    return _HighlightBatch(reply="Found some investors.", items=items)


def test_chat_endpoint_returns_cards(client, monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "interpret_query", _stub_interpret_vc)
    monkeypatch.setattr(chat_module, "write_highlights", _stub_write_highlights)

    event_id = make_event("chat-test-event")
    conn = get_conn()
    try:
        add_person(
            conn, event_id, name="Chat VC Person", confirmed=False,
            headline="Partner at Chat Ventures", field="finance_investing",
            industries=[Industry.VC_INVESTOR.value],
        )
    finally:
        conn.close()

    response = client.post(f"/api/events/{event_id}/chat", json={"message": "show me VCs"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["cards"]) == 1
    assert body["cards"][0]["name"] == "Chat VC Person"

    thread_response = client.get(f"/api/events/{event_id}/chat/{body['thread_id']}")
    assert thread_response.status_code == 200
    assert len(thread_response.json()["messages"]) == 2


def test_chat_endpoint_409_when_no_people(client) -> None:
    event_id = make_event("empty-chat-event")
    response = client.post(f"/api/events/{event_id}/chat", json={"message": "hi"})
    assert response.status_code == 409


def test_delete_event(client) -> None:
    event_id = make_event("delete-me-event")
    response = client.delete(f"/api/events/{event_id}")
    assert response.status_code == 204
    assert client.get(f"/api/events/{event_id}").status_code == 404

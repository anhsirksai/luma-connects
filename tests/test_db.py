from __future__ import annotations

from invite_finder import db
from invite_finder.store import chat_store, event_store, people_store, run_store


def make_conn():
    return db.connect(":memory:")


def test_migrations_apply_idempotently() -> None:
    conn = make_conn()
    version_before = conn.execute(
        "SELECT MAX(version) AS v FROM schema_version"
    ).fetchone()["v"]
    db.apply_migrations(conn)  # second call should be a no-op
    version_after = conn.execute(
        "SELECT MAX(version) AS v FROM schema_version"
    ).fetchone()["v"]
    assert version_before == version_after == 1


def _make_event(conn) -> int:
    return event_store.upsert_event(
        conn,
        luma_slug="vla-night-panel",
        luma_api_id="evt-1",
        source_url="https://luma.com/vla-night-panel",
        name="VLA Night Panel",
        description="A panel on VLA models.",
        cover_url="https://images.lumacdn.com/cover.jpg",
        start_at="2026-08-13T16:30:00.000Z",
        end_at="2026-08-14T01:30:00.000Z",
        timezone="America/Los_Angeles",
        location_type="offline",
        venue_name="AWS Builder Loft",
        address="525 Market St",
        city="San Francisco",
        region="California",
        country="United States",
        latitude=37.7936,
        longitude=-122.3965,
        guest_count=552,
        show_guest_list=1,
        categories_json='["Technology"]',
        ingest_source="luma_api",
        ingest_warnings_json="[]",
        raw_json="{}",
    )


def test_event_round_trip_and_upsert_updates_in_place() -> None:
    conn = make_conn()
    event_id = _make_event(conn)

    row = event_store.get_by_slug(conn, "vla-night-panel")
    assert row is not None
    assert row["id"] == event_id
    assert row["guest_count"] == 552
    assert event_store.categories(row) == ["Technology"]

    # Re-ingesting the same slug updates rather than duplicating.
    event_id_2 = _make_event(conn)
    assert event_id_2 == event_id
    events, total = event_store.list_events(conn)
    assert total == 1
    assert len(events) == 1


def test_person_event_classification_round_trip() -> None:
    conn = make_conn()
    event_id = _make_event(conn)

    person_id = people_store.upsert_person(
        conn,
        linkedin_url="https://www.linkedin.com/in/karim-baba-130547289",
        luma_user_api_id="usr-guest0001",
        name="Karim Baba",
        headline="Robotics engineer",
        bio_short="Robotics engineer working on imitation learning.",
    )
    assert person_id > 0

    # Upserting again with the same linkedin_url merges instead of duplicating,
    # and preserves fields not present in the second call.
    person_id_2 = people_store.upsert_person(
        conn,
        linkedin_url="https://www.linkedin.com/in/karim-baba-130547289",
        company="Nimbus Robotics",
    )
    assert person_id_2 == person_id
    person = people_store.get_person(conn, person_id)
    assert person["name"] == "Karim Baba"
    assert person["company"] == "Nimbus Robotics"

    people_store.link_person_to_event(
        conn,
        event_id=event_id,
        person_id=person_id,
        relation="featured_guest",
        is_confirmed=True,
        evidence=["Featured guest on Luma event page"],
    )

    people_store.upsert_classification(
        conn,
        person_id=person_id,
        input_fingerprint="fp-1",
        taxonomy_version=1,
        field="software_engineering",
        field_other_label=None,
        role_type="working_professional",
        seniority="mid",
        industries=["robotics_hardware"],
        tags=["robotics"],
        confidence=0.8,
        method="rules",
        model=None,
    )

    rows, total = people_store.list_event_people(conn, event_id)
    assert total == 1
    assert rows[0]["field"] == "software_engineering"
    assert rows[0]["is_confirmed"] == 1

    counts = people_store.count_event_people(conn, event_id)
    assert counts == {"total": 1, "confirmed": 1, "inferred": 0}
    assert people_store.count_classified(conn, event_id) == 1
    assert people_store.top_tags(conn, event_id) == ["robotics"]

    # Filtering by field that doesn't match returns zero rows.
    rows, total = people_store.list_event_people(conn, event_id, field="sales_gtm")
    assert total == 0


def test_run_lifecycle_and_events() -> None:
    conn = make_conn()
    event_id = _make_event(conn)

    run_id = run_store.create_run(conn, event_id=event_id, input_url="https://luma.com/x")
    run_store.update_status(conn, run_id, status="running", phase="luma_fetch", started=True)

    seq1 = run_store.append_event(conn, run_id, type_="phase", message="Fetching event")
    seq2 = run_store.append_event(conn, run_id, type_="log", message="Found 12 guests")
    assert seq2 == seq1 + 1

    run_store.update_status(conn, run_id, status="succeeded", phase="done", finished=True)
    run = run_store.get_run(conn, run_id)
    assert run["status"] == "succeeded"

    events = run_store.list_events_after(conn, run_id, after_seq=0)
    assert [e["seq"] for e in events] == [1, 2]

    events_after_1 = run_store.list_events_after(conn, run_id, after_seq=1)
    assert len(events_after_1) == 1
    assert events_after_1[0]["message"] == "Found 12 guests"


def test_reap_stuck_runs_marks_failed() -> None:
    conn = make_conn()
    event_id = _make_event(conn)
    run_id = run_store.create_run(conn, event_id=event_id, input_url="https://luma.com/x")
    run_store.update_status(conn, run_id, status="running", started=True)

    reaped = run_store.reap_stuck_runs(conn)
    assert reaped == 1
    assert run_store.get_run(conn, run_id)["status"] == "failed"


def test_chat_thread_and_messages() -> None:
    conn = make_conn()
    event_id = _make_event(conn)

    thread_id = chat_store.create_thread(conn, event_id)
    chat_store.add_message(conn, thread_id, role="user", content="show me VCs")
    chat_store.add_message(
        conn,
        thread_id,
        role="assistant",
        content="Here are 2 investors.",
        filters={"industries": ["vc_investor"]},
        cards=[{"person_id": 1}],
    )

    messages = chat_store.list_messages(conn, thread_id)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"

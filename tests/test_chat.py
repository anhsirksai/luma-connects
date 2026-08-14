from __future__ import annotations

import pytest

from invite_finder import chat as chat_module
from invite_finder import db
from invite_finder.store import event_store, people_store, run_store


def make_event(conn) -> int:
    return event_store.upsert_event(
        conn,
        luma_slug="vla-night-panel",
        luma_api_id="evt-1",
        source_url="https://luma.com/vla-night-panel",
        name="VLA Night Panel",
        description="A panel on VLA models.",
        cover_url=None,
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


def add_person(conn, event_id, *, name, headline, company, field, role_type, seniority, industries, tags=None):
    person_id = people_store.upsert_person(
        conn,
        linkedin_url=f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}",
        name=name,
        headline=headline,
        company=company,
    )
    people_store.link_person_to_event(
        conn, event_id=event_id, person_id=person_id, relation="inferred",
        is_confirmed=False, relevance_score=80,
    )
    people_store.upsert_classification(
        conn, person_id=person_id, input_fingerprint=f"fp-{person_id}", taxonomy_version=1,
        field=field, field_other_label=None, role_type=role_type, seniority=seniority,
        industries=industries, tags=tags or [], confidence=0.7, method="rules", model=None,
    )
    return person_id


@pytest.fixture()
def seeded_event():
    conn = db.connect(":memory:")
    event_id = make_event(conn)
    vc_ids = [
        add_person(
            conn, event_id, name="Daniel Osei", headline="Partner at Frontier Robotics Ventures",
            company="Frontier Robotics Ventures", field="finance_investing",
            role_type="working_professional", seniority="leadership", industries=["vc_investor"],
            tags=["investing"],
        ),
        add_person(
            conn, event_id, name="Jordan Kim", headline="General Partner, Ridgeline Ventures",
            company="Ridgeline Ventures", field="finance_investing",
            role_type="working_professional", seniority="leadership", industries=["vc_investor"],
            tags=["investing"],
        ),
        add_person(
            conn, event_id, name="Morgan Lee", headline="Investor, early-stage robotics",
            company="Summit Capital", field="finance_investing",
            role_type="working_professional", seniority="leadership", industries=["vc_investor"],
            tags=["investing"],
        ),
    ]
    eng_id = add_person(
        conn, event_id, name="Dana Okafor", headline="Senior Software Engineer",
        company="Nimbus Robotics", field="software_engineering",
        role_type="working_professional", seniority="senior", industries=[],
    )
    return conn, event_id, vc_ids, eng_id


async def _stub_interpret_vc(message, *, model, available_tags):
    from invite_finder.chat import PersonFilter
    from invite_finder.taxonomy import Industry

    return PersonFilter(industries=[Industry.VC_INVESTOR], interpretation="Looking for investors")


async def _stub_write_highlights_passthrough(message, *, model, shortlist):
    from invite_finder.chat import _HighlightBatch, _HighlightItem

    items = [
        _HighlightItem(person_id=p["id"], highlight=f"Highlight for {p['name']}", why_relevant="Matches VC query")
        for p in shortlist
    ]
    return _HighlightBatch(reply="Here are the investors I found.", items=items)


async def test_vc_query_returns_only_the_vc_people(seeded_event, monkeypatch) -> None:
    conn, event_id, vc_ids, eng_id = seeded_event
    monkeypatch.setattr(chat_module, "interpret_query", _stub_interpret_vc)
    monkeypatch.setattr(chat_module, "write_highlights", _stub_write_highlights_passthrough)

    response = await chat_module.answer_chat_query(
        conn, event_id, message="show me potential VCs", thread_id=None, model="gpt-5.5"
    )

    assert response.used_fallback is False
    person_ids = [c.person_id for c in response.cards]
    assert set(vc_ids) == set(person_ids)
    assert eng_id not in person_ids
    assert all(c.linkedin_url is not None for c in response.cards)
    assert all(c.highlight.startswith("Highlight for") for c in response.cards)


async def test_nonsense_query_triggers_fallback(seeded_event, monkeypatch) -> None:
    conn, event_id, vc_ids, eng_id = seeded_event

    async def stub_interpret_empty(message, *, model, available_tags):
        from invite_finder.chat import PersonFilter

        return PersonFilter(interpretation="Unclear request")

    async def stub_fallback_empty(message, *, model, roster):
        from invite_finder.chat import _FallbackPick

        return _FallbackPick(person_ids=[], reason="Nothing matched.")

    monkeypatch.setattr(chat_module, "interpret_query", stub_interpret_empty)
    monkeypatch.setattr(chat_module, "fallback_pick", stub_fallback_empty)

    response = await chat_module.answer_chat_query(
        conn, event_id, message="asdkjfhaskdjfh", thread_id=None, model="gpt-5.5"
    )

    assert response.used_fallback is True
    assert response.cards == []
    assert any("No attendees" in c for c in response.caveats)


async def test_chat_thread_persists_messages(seeded_event, monkeypatch) -> None:
    conn, event_id, vc_ids, eng_id = seeded_event
    monkeypatch.setattr(chat_module, "interpret_query", _stub_interpret_vc)
    monkeypatch.setattr(chat_module, "write_highlights", _stub_write_highlights_passthrough)

    response = await chat_module.answer_chat_query(
        conn, event_id, message="show me potential VCs", thread_id=None, model="gpt-5.5"
    )

    from invite_finder.store import chat_store

    messages = chat_store.list_messages(conn, response.thread_id)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"

    # A second turn reuses the same thread.
    response_2 = await chat_module.answer_chat_query(
        conn, event_id, message="anyone else?", thread_id=response.thread_id, model="gpt-5.5"
    )
    assert response_2.thread_id == response.thread_id
    messages_after = chat_store.list_messages(conn, response.thread_id)
    assert len(messages_after) == 4

from invite_finder.agent import (
    build_seed_search_queries,
    dedupe_results,
    ensure_linkedin_city_query,
    normalize_linkedin_profile_url,
)


def test_normalize_linkedin_profile_url_strips_tracking() -> None:
    url = "https://www.linkedin.com/in/example-person/?trk=public_profile"

    assert normalize_linkedin_profile_url(url) == "https://www.linkedin.com/in/example-person"


def test_normalize_linkedin_profile_url_strips_text_fragment() -> None:
    url = "https://www.linkedin.com/in/example-person#:~:text=San%20Francisco"

    assert normalize_linkedin_profile_url(url) == "https://www.linkedin.com/in/example-person"


def test_dedupe_results_keeps_first_profile_hit() -> None:
    results = [
        {
            "title": "Example Person - Founder",
            "link": "https://www.linkedin.com/in/example-person/?trk=one",
            "description": "San Francisco founder",
            "rank": 1,
            "source_query": "query one",
        },
        {
            "title": "Example Person",
            "link": "https://www.linkedin.com/in/example-person/",
            "description": "Duplicate",
            "rank": 2,
            "source_query": "query two",
        },
    ]

    assert dedupe_results(results) == [
        {
            "title": "Example Person - Founder",
            "link": "https://www.linkedin.com/in/example-person",
            "description": "San Francisco founder",
            "rank": 1,
            "source_query": "query one",
        }
    ]


def test_ensure_linkedin_city_query_uses_bay_area_aliases() -> None:
    query = ensure_linkedin_city_query("robotics founder", "San Francisco")

    assert query.startswith("site:linkedin.com/in/")
    assert '"San Francisco Bay Area"' in query
    assert '"Bay Area"' in query
    assert "robotics founder" in query


def test_ensure_linkedin_city_query_does_not_duplicate_city_alias() -> None:
    query = ensure_linkedin_city_query(
        'site:linkedin.com/in/ "Bay Area" robotics',
        "San Francisco",
    )

    assert query == 'site:linkedin.com/in/ "Bay Area" robotics'


def test_seed_queries_for_vla_start_broad_before_exact_vla() -> None:
    queries = build_seed_search_queries("San Francisco", "https://luma.com/vla-night-panel")

    assert "robotics" in queries[0]
    assert "VLA" not in queries[0]
    assert "VLA" in queries[-1]


def test_build_seed_search_queries_for_event_uses_topics_from_event_metadata() -> None:
    from invite_finder.agent import build_seed_search_queries_for_event

    queries = build_seed_search_queries_for_event(
        city="San Francisco",
        event_name="[Fireside Discussion] From Tokens to Robots",
        categories=["Robotics", "AI"],
        description="Join us for an evening exploring vision-language-action models.",
    )

    assert len(queries) == 4
    assert all("site:linkedin.com/in/" in q for q in queries)
    assert any("Robotics" in q or "AI" in q for q in queries)
    # Stopwords like "from"/"the" should never appear as bare topic terms.
    assert "OR from OR" not in queries[0]


def test_build_seed_search_queries_for_event_falls_back_without_topics() -> None:
    from invite_finder.agent import build_seed_search_queries_for_event

    queries = build_seed_search_queries_for_event(
        city="Austin", event_name="TBD", categories=[], description=None
    )
    assert "founder OR engineer OR researcher OR investor OR operator" in queries[0]

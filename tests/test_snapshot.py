from __future__ import annotations

from invite_finder.snapshot import build_room_snapshot, largest_remainder_percentages, top_field_bars


def test_percentages_sum_to_100_for_screenshot_like_distribution() -> None:
    counts = {
        "software_engineering": 18,
        "data_ml": 17,
        "business_operations": 16,
        "research_academia": 12,
        "sales_gtm": 10,
        "finance_investing": 6,
        "other": 21,
    }
    percentages = largest_remainder_percentages(counts)
    assert sum(percentages.values()) == 100


def test_percentages_sum_to_100_for_seven_equal_items() -> None:
    counts = {str(i): 1 for i in range(7)}
    percentages = largest_remainder_percentages(counts)
    assert sum(percentages.values()) == 100


def test_percentages_for_single_item_is_100() -> None:
    percentages = largest_remainder_percentages({"only": 5})
    assert percentages == {"only": 100}


def test_percentages_for_zero_items_is_empty() -> None:
    percentages = largest_remainder_percentages({})
    assert percentages == {}
    percentages_zero_counts = largest_remainder_percentages({"a": 0, "b": 0})
    assert percentages_zero_counts == {"a": 0, "b": 0}


def test_other_and_unknown_sort_last_regardless_of_size() -> None:
    classifications = [
        {"field": "other", "role_type": "working_professional", "seniority": "unknown", "industries": []}
        for _ in range(5)
    ] + [
        {"field": "sales_gtm", "role_type": "working_professional", "seniority": "unknown", "industries": []}
        for _ in range(1)
    ]
    snapshot = build_room_snapshot(
        classifications, registered_count=100, confirmed_people=2, inferred_people=4
    )
    field_section = next(s for s in snapshot.sections if s.id == "fields")
    assert field_section.bars[-1].key == "other"
    assert field_section.bars[0].key == "sales_gtm"

    seniority_section = next(s for s in snapshot.sections if s.id == "seniority")
    assert seniority_section.bars[-1].key == "unknown"


def test_basis_and_disclaimer_reflect_sample_vs_registered() -> None:
    classifications = [
        {"field": "software_engineering", "role_type": "founder", "seniority": "leadership", "industries": ["vc_investor"]},
        {"field": "data_ml", "role_type": "working_professional", "seniority": "mid", "industries": []},
    ]
    snapshot = build_room_snapshot(
        classifications, registered_count=552, confirmed_people=1, inferred_people=1
    )
    assert snapshot.basis.registered_count == 552
    assert snapshot.basis.confirmed_people == 1
    assert snapshot.basis.inferred_people == 1
    assert snapshot.basis.classified_people == 2
    assert "552" in snapshot.basis.disclaimer
    assert "1 confirmed" in snapshot.basis.disclaimer
    assert "1 inferred" in snapshot.basis.disclaimer


def test_top_field_bars_returns_top_three() -> None:
    classifications = [
        {"field": "software_engineering", "role_type": "working_professional", "seniority": "mid", "industries": []},
        {"field": "software_engineering", "role_type": "working_professional", "seniority": "mid", "industries": []},
        {"field": "data_ml", "role_type": "working_professional", "seniority": "mid", "industries": []},
        {"field": "sales_gtm", "role_type": "working_professional", "seniority": "mid", "industries": []},
        {"field": "other", "role_type": "working_professional", "seniority": "mid", "industries": []},
    ]
    snapshot = build_room_snapshot(
        classifications, registered_count=None, confirmed_people=5, inferred_people=0
    )
    top = top_field_bars(snapshot, limit=3)
    assert len(top) == 3
    assert top[0].key == "software_engineering"
    assert "other" not in [b.key for b in top]


def test_empty_classifications_produce_no_sections() -> None:
    snapshot = build_room_snapshot([], registered_count=100, confirmed_people=0, inferred_people=0)
    assert snapshot.sections == []
    assert snapshot.basis.classified_people == 0

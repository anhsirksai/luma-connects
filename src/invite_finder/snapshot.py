from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

from invite_finder.taxonomy import FIELD_LABELS, INDUSTRY_LABELS, ROLE_TYPE_LABELS, SENIORITY_LABELS


class SnapshotBar(BaseModel):
    key: str
    label: str
    count: int
    percentage: int


class SnapshotSection(BaseModel):
    id: Literal["fields", "role_types", "seniority", "industries"]
    title: str
    bars: list[SnapshotBar]


class SnapshotBasis(BaseModel):
    registered_count: int | None
    confirmed_people: int
    inferred_people: int
    classified_people: int
    disclaimer: str


class RoomSnapshot(BaseModel):
    sections: list[SnapshotSection]
    basis: SnapshotBasis
    generated_at: datetime


def _to_value_keyed(labels: dict[Any, str]) -> dict[str, str]:
    return {member.value: label for member, label in labels.items()}


FIELD_LABELS_BY_VALUE = _to_value_keyed(FIELD_LABELS)
ROLE_TYPE_LABELS_BY_VALUE = _to_value_keyed(ROLE_TYPE_LABELS)
SENIORITY_LABELS_BY_VALUE = _to_value_keyed(SENIORITY_LABELS)
INDUSTRY_LABELS_BY_VALUE = _to_value_keyed(INDUSTRY_LABELS)


def largest_remainder_percentages(counts: dict[str, int]) -> dict[str, int]:
    """Allocate integer percentages that sum to exactly 100 (Hare-Niemeyer /
    largest-remainder method). Naive rounding can land on 99 or 101."""
    total = sum(counts.values())
    if total == 0:
        return {k: 0 for k in counts}

    exact = {k: (v * 100) / total for k, v in counts.items()}
    floors = {k: int(e) for k, e in exact.items()}
    remainder_budget = 100 - sum(floors.values())

    ordered_keys = sorted(counts.keys(), key=lambda k: exact[k] - floors[k], reverse=True)
    result = dict(floors)
    for key in ordered_keys[:remainder_budget]:
        result[key] += 1
    return result


def _build_section(
    section_id: Literal["fields", "role_types", "seniority", "industries"],
    title: str,
    counts: dict[str, int],
    labels: dict[str, str],
    *,
    pin_last: set[str],
) -> SnapshotSection:
    percentages = largest_remainder_percentages(counts)
    bars = [
        SnapshotBar(key=key, label=labels.get(key, key), count=count, percentage=percentages[key])
        for key, count in counts.items()
    ]
    bars.sort(key=lambda bar: (bar.key in pin_last, -bar.count))
    return SnapshotSection(id=section_id, title=title, bars=bars)


def build_room_snapshot(
    classifications: list[dict[str, Any]],
    *,
    registered_count: int | None,
    confirmed_people: int,
    inferred_people: int,
) -> RoomSnapshot:
    """`classifications` is a list of {"field", "role_type", "seniority",
    "industries": [...]} dicts, one per classified person at the event."""
    field_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    seniority_counts: dict[str, int] = {}
    industry_counts: dict[str, int] = {}

    for row in classifications:
        field_counts[row["field"]] = field_counts.get(row["field"], 0) + 1
        role_counts[row["role_type"]] = role_counts.get(row["role_type"], 0) + 1
        seniority_counts[row["seniority"]] = seniority_counts.get(row["seniority"], 0) + 1
        for industry in row.get("industries") or []:
            industry_counts[industry] = industry_counts.get(industry, 0) + 1

    sections: list[SnapshotSection] = []
    if field_counts:
        sections.append(
            _build_section("fields", "Most common fields", field_counts, FIELD_LABELS_BY_VALUE, pin_last={"other"})
        )
    if role_counts:
        sections.append(
            _build_section("role_types", "Professional roles", role_counts, ROLE_TYPE_LABELS_BY_VALUE, pin_last=set())
        )
    if seniority_counts:
        sections.append(
            _build_section("seniority", "Seniority", seniority_counts, SENIORITY_LABELS_BY_VALUE, pin_last={"unknown"})
        )
    if industry_counts:
        sections.append(
            _build_section("industries", "Industries", industry_counts, INDUSTRY_LABELS_BY_VALUE, pin_last={"other"})
        )

    classified_people = len(classifications)
    disclaimer = (
        f"Estimated from {confirmed_people} confirmed and {inferred_people} "
        f"inferred public profiles."
    )
    if registered_count:
        disclaimer += (
            f" {registered_count} people are registered; Luma does not publish "
            "the full guest list."
        )

    basis = SnapshotBasis(
        registered_count=registered_count,
        confirmed_people=confirmed_people,
        inferred_people=inferred_people,
        classified_people=classified_people,
        disclaimer=disclaimer,
    )
    return RoomSnapshot(sections=sections, basis=basis, generated_at=datetime.now(timezone.utc))


def top_field_bars(snapshot: RoomSnapshot, limit: int = 3) -> list[SnapshotBar]:
    for section in snapshot.sections:
        if section.id == "fields":
            return section.bars[:limit]
    return []

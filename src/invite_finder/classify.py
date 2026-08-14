from __future__ import annotations

import hashlib
import sqlite3

from agents import Agent, Runner
from pydantic import BaseModel, Field

from invite_finder.classify_rules import ClassificationHint, apply_rules, hint_to_labels
from invite_finder.observability import NullReporter, RunReporter
from invite_finder.store import people_store
from invite_finder.taxonomy import (
    TAXONOMY_VERSION,
    FieldCategory,
    Industry,
    RoleType,
    Seniority,
    field_values,
    industry_values,
    role_type_values,
    seniority_values,
)

BATCH_SIZE = 25


class PersonInput(BaseModel):
    person_ref: str
    name: str | None = None
    headline: str | None = None
    company: str | None = None
    bio_short: str | None = None


class PersonLabels(BaseModel):
    person_ref: str
    field: FieldCategory
    field_other_label: str | None = None
    role_type: RoleType
    seniority: Seniority
    industries: list[Industry] = Field(default_factory=list, max_length=3)
    tags: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0, le=1)


class ClassificationBatch(BaseModel):
    labels: list[PersonLabels]


def compute_input_fingerprint(
    *,
    name: str | None,
    headline: str | None,
    company: str | None,
    bio_short: str | None,
    taxonomy_version: int = TAXONOMY_VERSION,
) -> str:
    basis = f"{taxonomy_version}|{name or ''}|{headline or ''}|{company or ''}|{bio_short or ''}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def build_classification_agent(model: str) -> Agent:
    return Agent(
        name="Event attendee classifier",
        model=model,
        output_type=ClassificationBatch,
        instructions=(
            "You label event attendees from public profile text only. For each "
            "person, assign exactly one field, one role_type, one seniority, up "
            "to 3 industries, and up to 6 short lowercase tags. Use the given "
            "text only -- do not invent employers, titles, or affiliations. "
            "Prefer 'other' or 'unknown' over guessing when the text is too thin "
            "to tell. Never infer gender, ethnicity, age, or any other protected "
            "attribute. Return exactly one label per person_ref you were given, "
            "reusing the same person_ref values verbatim."
        ),
    )


def _hint_text(hint: ClassificationHint | None) -> str:
    if not hint:
        return ""
    parts = []
    if hint.field:
        parts.append(f"field~{hint.field.value}")
    if hint.role_type:
        parts.append(f"role~{hint.role_type.value}")
    if hint.seniority:
        parts.append(f"seniority~{hint.seniority.value}")
    if hint.industries:
        parts.append("industries~" + ",".join(i.value for i in hint.industries))
    return f" [rule hints: {'; '.join(parts)}]" if parts else ""


async def classify_batch(
    people: list[PersonInput],
    *,
    model: str,
    hints: dict[str, ClassificationHint] | None = None,
) -> ClassificationBatch:
    """The LLM seam for a single batch (<=25 people). Tests stub this function
    directly instead of mocking the OpenAI SDK."""
    hints = hints or {}
    agent = build_classification_agent(model)
    lines = [
        f"- person_ref={p.person_ref} | name={p.name or ''} | "
        f"headline={p.headline or ''} | company={p.company or ''} | "
        f"bio={p.bio_short or ''}{_hint_text(hints.get(p.person_ref))}"
        for p in people
    ]
    prompt = (
        f"Classify these {len(people)} event attendees using this taxonomy.\n"
        f"field: {', '.join(field_values())}\n"
        f"role_type: {', '.join(role_type_values())}\n"
        f"seniority: {', '.join(seniority_values())}\n"
        f"industries: {', '.join(industry_values())}\n\n"
        + "\n".join(lines)
    )
    result = await Runner.run(agent, prompt, max_turns=4)
    return result.final_output


async def classify_event_people(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    model: str,
    use_llm: bool = True,
    taxonomy_version: int = TAXONOMY_VERSION,
    reporter: RunReporter | None = None,
) -> int:
    """Classify every person linked to `event_id` whose current text hasn't
    already been classified under `taxonomy_version`. Rules run first as a
    hint (and as the complete fallback when use_llm=False); an LLM batch pass
    then produces the final labels. Returns the number of people classified."""
    reporter = reporter or NullReporter()
    rows, _ = people_store.list_event_people(conn, event_id, limit=10_000)

    pending: list[PersonInput] = []
    hints_by_ref: dict[str, ClassificationHint] = {}
    fingerprints_by_ref: dict[str, str] = {}
    person_id_by_ref: dict[str, int] = {}

    for row in rows:
        person_id = row["id"]
        fp = compute_input_fingerprint(
            name=row["name"],
            headline=row["headline"],
            company=row["company"],
            bio_short=row["bio_short"],
            taxonomy_version=taxonomy_version,
        )
        existing = people_store.get_classification(conn, person_id)
        if (
            existing
            and existing["input_fingerprint"] == fp
            and existing["taxonomy_version"] == taxonomy_version
        ):
            continue

        ref = str(person_id)
        hints_by_ref[ref] = apply_rules(
            name=row["name"], headline=row["headline"],
            company=row["company"], bio_short=row["bio_short"],
        )
        fingerprints_by_ref[ref] = fp
        person_id_by_ref[ref] = person_id
        pending.append(
            PersonInput(
                person_ref=ref, name=row["name"], headline=row["headline"],
                company=row["company"], bio_short=row["bio_short"],
            )
        )

    if not pending:
        return 0

    def _write_fallback(ref: str) -> None:
        fallback = hint_to_labels(hints_by_ref[ref])
        people_store.upsert_classification(
            conn, person_id=person_id_by_ref[ref],
            input_fingerprint=fingerprints_by_ref[ref],
            taxonomy_version=taxonomy_version, method="rules", model=None,
            **fallback,
        )

    if not use_llm:
        for person in pending:
            _write_fallback(person.person_ref)
        return len(pending)

    classified = 0
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i : i + BATCH_SIZE]
        reporter.emit(
            "log",
            f"Classifying {len(batch)} people ({i + 1}-{i + len(batch)} of {len(pending)})",
        )
        try:
            result = await classify_batch(batch, model=model, hints=hints_by_ref)
            by_ref = {label.person_ref: label for label in result.labels}
        except Exception as exc:  # noqa: BLE001 - fall back to rules per person
            reporter.emit("log", f"Classification batch failed, using rules fallback: {exc}")
            by_ref = {}

        for person in batch:
            ref = person.person_ref
            label = by_ref.get(ref)
            if label is None:
                _write_fallback(ref)
            else:
                people_store.upsert_classification(
                    conn, person_id=person_id_by_ref[ref],
                    input_fingerprint=fingerprints_by_ref[ref],
                    taxonomy_version=taxonomy_version, field=label.field.value,
                    field_other_label=label.field_other_label,
                    role_type=label.role_type.value, seniority=label.seniority.value,
                    industries=[i.value for i in label.industries],
                    tags=label.tags, confidence=label.confidence,
                    method="llm", model=model,
                )
            classified += 1

    return classified

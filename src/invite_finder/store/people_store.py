from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_identity_key(
    *,
    linkedin_url: str | None,
    luma_user_api_id: str | None,
    name: str | None,
    company: str | None,
) -> str:
    if linkedin_url:
        return f"li:{linkedin_url.lower().rstrip('/')}"
    if luma_user_api_id:
        return f"luma:{luma_user_api_id}"
    basis = f"{(name or '').strip().lower()}|{(company or '').strip().lower()}"
    return f"anon:{hashlib.sha256(basis.encode()).hexdigest()[:16]}"


def upsert_person(
    conn: sqlite3.Connection,
    *,
    linkedin_url: str | None = None,
    luma_user_api_id: str | None = None,
    name: str | None = None,
    headline: str | None = None,
    company: str | None = None,
    location_text: str | None = None,
    avatar_url: str | None = None,
    bio_short: str | None = None,
    profile_text: str | None = None,
) -> int:
    """Insert or merge a person, keyed by identity_key. Non-null new fields win;
    existing non-null fields are preserved when the new value is None."""
    identity_key = compute_identity_key(
        linkedin_url=linkedin_url,
        luma_user_api_id=luma_user_api_id,
        name=name,
        company=company,
    )
    existing = conn.execute(
        "SELECT * FROM people WHERE identity_key = ?", (identity_key,)
    ).fetchone()
    now = now_iso()

    if existing:
        merged = {
            "linkedin_url": linkedin_url or existing["linkedin_url"],
            "luma_user_api_id": luma_user_api_id or existing["luma_user_api_id"],
            "name": name or existing["name"],
            "headline": headline or existing["headline"],
            "company": company or existing["company"],
            "location_text": location_text or existing["location_text"],
            "avatar_url": avatar_url or existing["avatar_url"],
            "bio_short": bio_short or existing["bio_short"],
            "profile_text": profile_text or existing["profile_text"],
        }
        conn.execute(
            """
            UPDATE people SET linkedin_url=?, luma_user_api_id=?, name=?, headline=?,
              company=?, location_text=?, avatar_url=?, bio_short=?, profile_text=?,
              updated_at=?
            WHERE id=?
            """,
            (*merged.values(), now, existing["id"]),
        )
        conn.commit()
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO people
          (identity_key, linkedin_url, luma_user_api_id, name, headline, company,
           location_text, avatar_url, bio_short, profile_text, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identity_key, linkedin_url, luma_user_api_id, name, headline, company,
            location_text, avatar_url, bio_short, profile_text, now, now,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def link_person_to_event(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    person_id: int,
    relation: str,
    is_confirmed: bool,
    relevance_score: int | None = None,
    relevance_rationale: str | None = None,
    city_signal: str | None = None,
    evidence: list[str] | None = None,
    source_queries: list[str] | None = None,
    run_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO event_people
          (event_id, person_id, relation, is_confirmed, relevance_score,
           relevance_rationale, city_signal, evidence_json, source_queries_json,
           run_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id, person_id) DO UPDATE SET
          relation=excluded.relation, is_confirmed=excluded.is_confirmed,
          relevance_score=excluded.relevance_score,
          relevance_rationale=excluded.relevance_rationale,
          city_signal=excluded.city_signal, evidence_json=excluded.evidence_json,
          source_queries_json=excluded.source_queries_json, run_id=excluded.run_id
        """,
        (
            event_id, person_id, relation, int(is_confirmed), relevance_score,
            relevance_rationale, city_signal, json.dumps(evidence or []),
            json.dumps(source_queries or []), run_id, now_iso(),
        ),
    )
    conn.commit()


def upsert_classification(
    conn: sqlite3.Connection,
    *,
    person_id: int,
    input_fingerprint: str,
    taxonomy_version: int,
    field: str,
    field_other_label: str | None,
    role_type: str,
    seniority: str,
    industries: list[str],
    tags: list[str],
    confidence: float,
    method: str,
    model: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO person_classifications
          (person_id, input_fingerprint, taxonomy_version, field, field_other_label,
           role_type, seniority, industries_json, tags_json, confidence, method,
           model, classified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(person_id) DO UPDATE SET
          input_fingerprint=excluded.input_fingerprint,
          taxonomy_version=excluded.taxonomy_version, field=excluded.field,
          field_other_label=excluded.field_other_label, role_type=excluded.role_type,
          seniority=excluded.seniority, industries_json=excluded.industries_json,
          tags_json=excluded.tags_json, confidence=excluded.confidence,
          method=excluded.method, model=excluded.model,
          classified_at=excluded.classified_at
        """,
        (
            person_id, input_fingerprint, taxonomy_version, field, field_other_label,
            role_type, seniority, json.dumps(industries), json.dumps(tags),
            confidence, method, model, now_iso(),
        ),
    )
    conn.commit()


def get_classification(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM person_classifications WHERE person_id = ?", (person_id,)
    ).fetchone()


def list_event_people(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    field: str | None = None,
    role_type: str | None = None,
    seniority: str | None = None,
    industry: str | None = None,
    confirmed: bool | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    clauses = ["ep.event_id = ?"]
    params: list[Any] = [event_id]

    if field:
        clauses.append("pc.field = ?")
        params.append(field)
    if role_type:
        clauses.append("pc.role_type = ?")
        params.append(role_type)
    if seniority:
        clauses.append("pc.seniority = ?")
        params.append(seniority)
    if industry:
        clauses.append("pc.industries_json LIKE ?")
        params.append(f'%"{industry}"%')
    if confirmed is not None:
        clauses.append("ep.is_confirmed = ?")
        params.append(int(confirmed))
    if q:
        clauses.append(
            "(p.name LIKE ? OR p.headline LIKE ? OR p.company LIKE ? OR p.bio_short LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])

    where = " AND ".join(clauses)
    base = """
        FROM event_people ep
        JOIN people p ON p.id = ep.person_id
        LEFT JOIN person_classifications pc ON pc.person_id = p.id
        WHERE {where}
    """.format(where=where)

    total = conn.execute(f"SELECT COUNT(*) AS n {base}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT p.*, ep.relation, ep.is_confirmed, ep.relevance_score,
               ep.relevance_rationale, ep.city_signal, ep.evidence_json,
               ep.source_queries_json, pc.field, pc.field_other_label, pc.role_type,
               pc.seniority, pc.industries_json AS pc_industries_json,
               pc.tags_json, pc.confidence AS pc_confidence
        {base}
        ORDER BY ep.is_confirmed DESC, ep.relevance_score DESC NULLS LAST,
                 pc.confidence DESC NULLS LAST
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    return list(rows), int(total)


def get_person(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()


def count_event_people(conn: sqlite3.Connection, event_id: int) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN is_confirmed = 1 THEN 1 ELSE 0 END) AS confirmed,
          SUM(CASE WHEN is_confirmed = 0 THEN 1 ELSE 0 END) AS inferred
        FROM event_people WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "confirmed": row["confirmed"] or 0,
        "inferred": row["inferred"] or 0,
    }


def count_classified(conn: sqlite3.Connection, event_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM event_people ep
        JOIN person_classifications pc ON pc.person_id = ep.person_id
        WHERE ep.event_id = ?
        """,
        (event_id,),
    ).fetchone()
    return int(row["n"] or 0)


def top_tags(conn: sqlite3.Connection, event_id: int, limit: int = 40) -> list[str]:
    rows = conn.execute(
        """
        SELECT pc.tags_json FROM event_people ep
        JOIN person_classifications pc ON pc.person_id = ep.person_id
        WHERE ep.event_id = ?
        """,
        (event_id,),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        for tag in json.loads(row["tags_json"] or "[]"):
            counts[tag] = counts.get(tag, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    return [tag for tag, _ in ordered[:limit]]

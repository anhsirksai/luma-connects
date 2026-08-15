from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);

        CREATE TABLE http_cache (
          fingerprint   TEXT PRIMARY KEY,
          kind          TEXT NOT NULL,
          url           TEXT NOT NULL,
          request_json  TEXT NOT NULL,
          status_code   INTEGER NOT NULL,
          content_type  TEXT,
          body          TEXT NOT NULL,
          byte_size     INTEGER NOT NULL,
          fetched_at    TEXT NOT NULL,
          expires_at    TEXT,
          hit_count     INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_http_cache_kind_url ON http_cache(kind, url);
        CREATE INDEX idx_http_cache_expires ON http_cache(expires_at);

        CREATE TABLE events (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          luma_slug         TEXT NOT NULL UNIQUE,
          luma_api_id       TEXT UNIQUE,
          source_url        TEXT NOT NULL,
          name              TEXT NOT NULL,
          description       TEXT,
          cover_url         TEXT,
          start_at          TEXT,
          end_at            TEXT,
          timezone          TEXT,
          location_type     TEXT,
          venue_name        TEXT,
          address           TEXT,
          city              TEXT,
          region            TEXT,
          country           TEXT,
          latitude          REAL,
          longitude         REAL,
          guest_count       INTEGER,
          show_guest_list   INTEGER NOT NULL DEFAULT 0,
          categories_json   TEXT NOT NULL DEFAULT '[]',
          ingest_source     TEXT NOT NULL,
          ingest_warnings_json TEXT NOT NULL DEFAULT '[]',
          raw_json          TEXT,
          created_at        TEXT NOT NULL,
          updated_at        TEXT NOT NULL
        );
        CREATE INDEX idx_events_start_at ON events(start_at);

        CREATE TABLE people (
          id             INTEGER PRIMARY KEY AUTOINCREMENT,
          identity_key   TEXT NOT NULL UNIQUE,
          linkedin_url   TEXT UNIQUE,
          luma_user_api_id TEXT UNIQUE,
          name           TEXT,
          headline       TEXT,
          company        TEXT,
          location_text  TEXT,
          avatar_url     TEXT,
          bio_short      TEXT,
          profile_text   TEXT,
          created_at     TEXT NOT NULL,
          updated_at     TEXT NOT NULL
        );

        CREATE TABLE runs (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id    INTEGER REFERENCES events(id) ON DELETE CASCADE,
          input_url   TEXT NOT NULL,
          status      TEXT NOT NULL,
          phase       TEXT,
          params_json TEXT NOT NULL DEFAULT '{}',
          stats_json  TEXT NOT NULL DEFAULT '{}',
          error       TEXT,
          started_at  TEXT,
          finished_at TEXT,
          created_at  TEXT NOT NULL
        );
        CREATE INDEX idx_runs_event ON runs(event_id, created_at);

        CREATE TABLE event_people (
          event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          person_id    INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
          relation     TEXT NOT NULL,
          is_confirmed INTEGER NOT NULL,
          relevance_score INTEGER,
          relevance_rationale TEXT,
          city_signal  TEXT,
          evidence_json TEXT NOT NULL DEFAULT '[]',
          source_queries_json TEXT NOT NULL DEFAULT '[]',
          run_id       INTEGER REFERENCES runs(id) ON DELETE SET NULL,
          created_at   TEXT NOT NULL,
          PRIMARY KEY (event_id, person_id)
        );
        CREATE INDEX idx_event_people_event
          ON event_people(event_id, is_confirmed, relevance_score);

        CREATE TABLE person_classifications (
          person_id        INTEGER PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
          input_fingerprint TEXT NOT NULL,
          taxonomy_version INTEGER NOT NULL,
          field            TEXT NOT NULL,
          field_other_label TEXT,
          role_type        TEXT NOT NULL,
          seniority        TEXT NOT NULL,
          industries_json  TEXT NOT NULL DEFAULT '[]',
          tags_json        TEXT NOT NULL DEFAULT '[]',
          confidence       REAL NOT NULL,
          method           TEXT NOT NULL,
          model            TEXT,
          classified_at    TEXT NOT NULL
        );
        CREATE INDEX idx_pc_fingerprint
          ON person_classifications(input_fingerprint, taxonomy_version);

        CREATE TABLE run_events (
          id        INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id    INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          seq       INTEGER NOT NULL,
          ts        TEXT NOT NULL,
          type      TEXT NOT NULL,
          message   TEXT NOT NULL,
          data_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE UNIQUE INDEX idx_run_events_seq ON run_events(run_id, seq);

        CREATE TABLE chat_threads (
          id         INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id   INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          title      TEXT,
          created_at TEXT NOT NULL
        );

        CREATE TABLE chat_messages (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          thread_id    INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
          role         TEXT NOT NULL,
          content      TEXT NOT NULL,
          filters_json TEXT,
          cards_json   TEXT,
          created_at   TEXT NOT NULL
        );
        CREATE INDEX idx_chat_messages_thread ON chat_messages(thread_id, id);
        """,
    ),
    (
        2,
        """
        -- Enrichment fields. Everything beyond a LinkedIn headline arrives
        -- from a paid per-call provider, so these are filled in incrementally
        -- by upsert_person's merge semantics rather than in one pass.
        ALTER TABLE people ADD COLUMN email TEXT;
        ALTER TABLE people ADD COLUMN phone TEXT;
        ALTER TABLE people ADD COLUMN x_url TEXT;
        ALTER TABLE people ADD COLUMN enrichment_json TEXT;

        -- A customer is identified by the messaging handle they text from.
        CREATE TABLE customers (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          handle        TEXT NOT NULL UNIQUE,
          chat_id       TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at  TEXT NOT NULL
        );

        CREATE TABLE orders (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          customer_id       INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
          event_id          INTEGER REFERENCES events(id) ON DELETE SET NULL,
          tier              TEXT NOT NULL,
          person_count      INTEGER NOT NULL,
          amount_cents      INTEGER NOT NULL,
          status            TEXT NOT NULL DEFAULT 'pending',
          stripe_session_id TEXT,
          created_at        TEXT NOT NULL,
          paid_at           TEXT,
          delivered_at      TEXT
        );
        CREATE INDEX idx_orders_customer ON orders(customer_id, created_at);
        CREATE INDEX idx_orders_status ON orders(status);

        CREATE TABLE entitlements (
          customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
          event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          tier        TEXT NOT NULL,
          order_id    INTEGER REFERENCES orders(id) ON DELETE SET NULL,
          granted_at  TEXT NOT NULL,
          PRIMARY KEY (customer_id, event_id, tier)
        );

        -- Checked before every outbound send. An address here is never
        -- contacted again on that channel, whatever the campaign.
        CREATE TABLE suppressions (
          id         INTEGER PRIMARY KEY AUTOINCREMENT,
          channel    TEXT NOT NULL,
          address    TEXT NOT NULL,
          reason     TEXT,
          created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_suppressions_addr ON suppressions(channel, address);

        -- The cost ledger. Every paid provider call lands here, quoted before
        -- it is bought, so spend can be compared against orders revenue.
        CREATE TABLE service_purchases (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          capability    TEXT NOT NULL,
          provider      TEXT NOT NULL,
          channel       TEXT NOT NULL,
          quote_cents   INTEGER,
          charged_cents INTEGER,
          purchase_id   TEXT,
          status        TEXT NOT NULL,
          cache_hit     INTEGER NOT NULL DEFAULT 0,
          person_id     INTEGER REFERENCES people(id) ON DELETE SET NULL,
          order_id      INTEGER REFERENCES orders(id) ON DELETE SET NULL,
          error         TEXT,
          created_at    TEXT NOT NULL
        );
        CREATE INDEX idx_service_purchases_created ON service_purchases(created_at);
        CREATE INDEX idx_service_purchases_capability ON service_purchases(capability);
        """,
    ),
    (
        3,
        """
        -- Money received that we could not match to an order.
        --
        -- Happens when the orders row is gone (an ephemeral filesystem wiped
        -- the DB between checkout and callback), when someone pays the raw
        -- Payment Link without going through the bot, or when a stale link is
        -- reused. Stripe is the durable record in all three cases; this table
        -- is our copy, so unfulfilled money is never merely dropped.
        CREATE TABLE orphan_payments (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          stripe_session_id TEXT UNIQUE,
          claimed_order_id  TEXT,
          amount_cents      INTEGER,
          email             TEXT,
          phone             TEXT,
          reason            TEXT NOT NULL,
          notified          INTEGER NOT NULL DEFAULT 0,
          resolved_at       TEXT,
          raw_json          TEXT,
          created_at        TEXT NOT NULL
        );
        CREATE INDEX idx_orphan_payments_unresolved
          ON orphan_payments(resolved_at, created_at);
        """,
    ),
]


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (and if needed create) the SQLite database at `path`, fully migrated."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may resolve a sync dependency (this
    # connection) in a worker thread and then use it from an async endpoint
    # on the event loop thread. Each connection is still only ever used
    # sequentially within one request/task, never concurrently from two
    # threads at once, so this is safe.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    apply_migrations(conn)
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] or 0


def apply_migrations(conn: sqlite3.Connection) -> None:
    current = _current_version(conn)
    for version, script in MIGRATIONS:
        if version <= current:
            continue
        conn.executescript(script)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.commit()

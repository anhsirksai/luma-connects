# Implementation Plan: Event Room Snapshot + Super Connector

> **How to use this document.** This is an executable implementation plan. Work the phases in §7 **in order** — each one ends in something runnable and independently verifiable. Do not skip ahead to the frontend; phases 1–8 are designed to need **no live Bright Data calls**, because the API zones in `.env` are currently invalid. Every file path, model, and endpoint below is concrete and intended to be implemented as written. Where the plan says "do not modify `brightdata.py`" or "delete this last", those are load-bearing constraints, not style preferences.

## Context

Today this repo is a CLI: given an event URL + city, an OpenAI Agents SDK agent runs Google SERP searches through Bright Data to discover LinkedIn profiles of people who *might be interested* in the event, scores them 0–100, and writes a JSON file to `reports/`. There is no Luma attendee ingestion, no classification taxonomy, no HTTP API, no persistence beyond flat files, and no chat layer. The existing Next.js app at `invite_viewer/` is a report-file browser that reads `../reports` off the local filesystem.

We want a BridgeUs-style product: paste a Luma link → see who's in the room, broken down by field / role / seniority → ask "show me potential VCs" or "people from big pharma" in a chat box → get person cards with a LinkedIn highlight and a link. That's backend work plus a frontend rebuild, done together.

Decisions already made (treat as fixed):
- **Sourcing = public Luma page + SERP inference.** Not a verified registration list — the UI must say so.
- **Rebuild inside `invite_viewer/`**, keeping Next.js/Tailwind/the Bright Data palette.
- **Persistence is a hard requirement.** Every Luma and Bright Data fetch is cached durably so repeat views cost nothing; the UI reads from storage. Only a cold event triggers a live run, with streaming progress.

### The one fact that shapes everything

`https://api.lu.ma/url?url=<slug>` returns rich public JSON with no auth — verified against `vla-night-panel`:
- `data.event`: `api_id`, `name`, `cover_url`, `start_at`/`end_at`, `timezone`, fully structured `geo_address_info` (city, region, country, coords), `show_guest_list`
- `data.guest_count` = **552**, `data.hosts[]`, `data.categories[]`, `data.description_mirror`
- `data.featured_guests[]` — 10 people, **10/10 with a non-null `linkedin_handle`** (e.g. `/in/karim-baba-130547289`)

But the full guest list is permanently closed: `api.lu.ma/event/get-guest-list` returns **401**. So for a 552-person event we get **~12 confirmed, LinkedIn-resolved people** (hosts + featured guests + speakers named in the description) and everything else is SERP inference.

**Therefore the ROOM SNAPSHOT percentages are an estimate over a sample.** This is designed into the schema, not papered over: `SnapshotBasis` is a required field carrying `registered_count` / `confirmed_people` / `inferred_people` / `classified_people` plus a server-generated disclaimer, and every person card carries a "Confirmed guest" vs "Likely relevant" pill.

---

## Architecture

```
Luma URL → luma.py (api.lu.ma JSON → JSON-LD → markdown fallbacks)
         → pipeline.py → agent.py (existing SERP discovery, reseeded from confirmed guests)
         → classify.py (rules pre-pass + batched LLM)
         → snapshot.py (rollup to percentage bars)
         → SQLite  ← FastAPI ← Next.js
              ↑
        CachingSession wraps every Bright Data call
```

---

## 1. Storage — stdlib `sqlite3`, WAL, hand-written migrations

No ORM. Zero new deps, and SQLite on a Fly volume (`/data`, `INVITE_DB_PATH=/data/invite_finder.db`) is the standard single-node pattern. Locally: `data/private/invite_finder.db` — already gitignored, which matters because this DB holds names and LinkedIn URLs.

**New:** `src/invite_finder/db.py` (connection factory, `MIGRATIONS`, `apply_migrations`; sets `row_factory=sqlite3.Row`, WAL, `foreign_keys=ON`, `busy_timeout=5000`) and `src/invite_finder/store/{cache,event,people,run,chat}_store.py` — repository functions taking `conn` as first arg, no global connection.

Tables (migration 1):

| Table | Purpose |
|---|---|
| `http_cache` | `fingerprint` PK, `kind`, `url`, `request_json`, `status_code`, `content_type`, `body`, `byte_size`, `fetched_at`, `expires_at`, `hit_count` |
| `events` | Luma metadata + `guest_count`, `show_guest_list`, `categories_json`, `ingest_source`, `ingest_warnings_json`, `raw_json` |
| `people` | one row per human: `identity_key` UNIQUE, `linkedin_url` UNIQUE, `luma_user_api_id` UNIQUE, name/headline/company/`location_text`/avatar/`bio_short`/`profile_text` |
| `event_people` | `(event_id, person_id)` PK, `relation` (`host`\|`featured_guest`\|`speaker`\|`inferred`), `is_confirmed`, `relevance_score`, `relevance_rationale`, `city_signal`, `evidence_json`, `source_queries_json`, `run_id` |
| `person_classifications` | `person_id` PK, `input_fingerprint`, `taxonomy_version`, `field`, `field_other_label`, `role_type`, `seniority`, `industries_json`, `tags_json`, `confidence`, `method`, `model` |
| `runs` | `event_id`, `input_url`, `status`, `phase`, `params_json`, `stats_json`, `error`, `started_at`, `finished_at` |
| `run_events` | durable SSE log: `(run_id, seq)` UNIQUE, `ts`, `type`, `message`, `data_json` |
| `chat_threads`, `chat_messages` | `role`, `content`, `filters_json`, `cards_json` |

Indexes: `http_cache(kind,url)`, `http_cache(expires_at)`, `events(start_at)`, `event_people(event_id,is_confirmed,relevance_score)`, `person_classifications(input_fingerprint,taxonomy_version)`, `runs(event_id,created_at)`, `chat_messages(thread_id,id)`. All FKs `ON DELETE CASCADE` (except `event_people.run_id` → `SET NULL`).

**Cache key:** `sha256` of the canonicalized Bright Data payload with `zone` and auth **stripped** (so renaming the currently-broken zones doesn't invalidate the cache) and the URL normalized (lowercase scheme+host, strip `utm_*`/`trk`/`fbclid`, strip trailing slash, sort remaining params). TTL by derived `kind`: `luma_api` 6h · `luma_page` 24h · `serp` 30d · `linkedin_profile` 30d · other 7d. Expired rows are **kept**, not deleted — served with `stale=True` in offline mode, which is what makes fixture-driven development work. `force_refresh=True` bypasses the read but still upserts.

**Wiring caching in without touching `brightdata.py`** — two layers:

- `src/invite_finder/cache.py` — `CachingSession(requests.Session)` overriding `request()`; on a hit it builds a synthetic `Response` (status, headers, `_content`) that `_post_request`'s existing parsing path handles unchanged. Injected via the constructor arg that already exists: `BrightDataClient(settings, session=CachingSession(conn))`. This catches *every* Bright Data call because they all funnel through `_post_request` (`brightdata.py:42`). `offline=True` makes a miss raise `CacheMiss` instead of hitting the network.
- `src/invite_finder/observability.py` — `ObservedBrightDataClient` duck-types the client and emits `run_events` rows around each call. Add a `WebDataClient` Protocol and change only the annotation on `LeadFinderContext.brightdata` (`agent.py:26`) — one line, no behavior change.

## 2. Luma ingestion — `src/invite_finder/luma.py`, `luma_models.py`

Three tiers, each recorded in `events.ingest_source` with degradations in `ingest_warnings_json` (surfaced to the UI — never silently degrade):

1. `unlock_url("https://api.lu.ma/url?url=<slug>", data_format=None)` — full fidelity.
   **`data_format` must be `None`.** The default `"markdown"` would run JSON through Bright Data's markdown converter and destroy it. `unlock_url` already only adds the key when truthy (`brightdata.py:131`), so no client change — but assert the body parses as JSON before trusting it, and fall through to tier 2 if not. Also handle both return shapes: `unlock_url` returns `body` if it's a string, else `json.dumps(body)`.
2. JSON-LD `schema.org/Event` from the HTML page — title/date/venue/cover, but **no** guest count and **no** LinkedIn handles.
3. Markdown via the existing `extract_event_page_context()` (`event.py:24`) — name + description only. Everything downstream must tolerate `guest_count=None, people=[]`.

Models:
- `LumaEvent` — `slug`, `source_url`, `luma_api_id`, `name`, `description`, `cover_url`, `start_at`, `end_at`, `timezone`, `location_type`, `venue: LumaVenue`, `guest_count`, `show_guest_list`, `categories`, `people: list[LumaPerson]`, `ingest_source: Literal["luma_api","luma_jsonld","luma_markdown","manual"]`, `warnings: list[str]`
- `LumaPerson` — `luma_user_api_id`, `name`, `first_name`, `last_name`, `bio_short`, `avatar_url`, `linkedin_url`, `linkedin_company_url`, `twitter_handle`, `website`, `relation: Literal["host","featured_guest","speaker"]`
- `LumaVenue` — `name`, `address`, `city`, `region`, `country`, `latitude`, `longitude`

Functions:
- `parse_luma_slug(url)` — accepts `lu.ma/x`, `luma.com/x`, `https://luma.com/x?tk=…`; rejects `/u/`, `/calendar/`, `/discover`; raises `LumaUrlError` → HTTP 400
- `linkedin_url_from_handle(handle)` — `/in/foo` → `("https://www.linkedin.com/in/foo", None)`; `/company/bar` → `(None, "https://www.linkedin.com/company/bar")`; bare `foo` → treat as `/in/foo`. Run through the existing `normalize_linkedin_profile_url` (`agent.py:36`).
- `parse_luma_api_payload(slug, payload)`, `parse_luma_jsonld(slug, html)`, `fetch_luma_event(client, url, *, allow_fallbacks=True)`
- `speaker_names_from_description(description)` — best-effort regex over "Speakers:" / bullet lists. **Low-confidence SERP hints only, never confirmed attendees.**

`geo_address_info.city` replaces the `--city` CLI flag entirely.

**Not reliably extractable** (say so in the README and the UI): the registration list (permanent 401); `featured_guests` may be 0 for small/private events; `bio_short` was empty for 8/10 guests, so headline/company for confirmed guests still needs SERP or a profile fetch. Scope v1 to a single occurrence for recurring events; record `recurrence_id` in `raw_json` only.

## 3. Taxonomy + classification — `taxonomy.py`, `classify_rules.py`, `classify.py`, `snapshot.py`

```python
TAXONOMY_VERSION = 1

class FieldCategory(str, Enum):   # exactly the 7 from the screenshot
    SOFTWARE_ENGINEERING = "software_engineering"
    DATA_ML              = "data_ml"
    BUSINESS_OPERATIONS  = "business_operations"
    RESEARCH_ACADEMIA    = "research_academia"
    SALES_GTM            = "sales_gtm"
    FINANCE_INVESTING    = "finance_investing"
    OTHER                = "other"

class RoleType(str, Enum):
    FOUNDER = "founder"; WORKING_PROFESSIONAL = "working_professional"
    STUDENT_EARLY_CAREER = "student_early_career"

class Seniority(str, Enum):
    LEADERSHIP = "leadership"   # C-level, VP, Head of, Partner, Director
    SENIOR = "senior"           # Senior/Staff/Principal/Lead
    MID = "mid"; JUNIOR = "junior"; UNKNOWN = "unknown"

class Industry(str, Enum):      # multi-label, max 3 per person
    VC_INVESTOR, BIOTECH_PHARMA, HEALTHCARE, AI_INFRA, ROBOTICS_HARDWARE,
    ENTERPRISE_SAAS, FINTECH, CONSUMER, CRYPTO_WEB3, CLIMATE_ENERGY,
    SECURITY, DEVTOOLS, CONSULTING_SERVICES, MEDIA_MARKETING,
    GOVERNMENT_NONPROFIT, EDUCATION, OTHER
```

`Seniority.UNKNOWN` is required — forcing a guess into `mid` would fabricate. Render it as its own segment or exclude it from the denominator with a footnote; do not hide it.

Free-text `tags: list[str]` (max 6, lowercase) covers the long tail ("big pharma", "clinical trials", "devrel", "growth") and is the second matching surface that makes "people from big pharma" work when the company is `Genentech`.

**Hybrid pipeline, in this order:**

1. **Rules pre-pass** (`classify_rules.py`, pure functions, no network) over `f"{name} | {headline} | {company} | {bio_short}"`:
   - `founder|co-?founder|ceo|building ` → `FOUNDER` + `LEADERSHIP`
   - `partner|general partner|principal at .*(Ventures|Capital|Partners|Fund)$|investor|angel` → `VC_INVESTOR` + `FINANCE_INVESTING`
   - `phd|postdoc|professor|research scientist|@ (Stanford|MIT|Berkeley|…)` → `RESEARCH_ACADEMIA`
   - `student|undergrad|ms candidate|incoming|new grad` → `STUDENT_EARLY_CAREER`
   - curated `BIG_PHARMA_COMPANIES` set (Pfizer, Genentech, Roche, Novartis, Merck, AstraZeneca, Moderna, Lilly, J&J, Sanofi, GSK, Amgen, Regeneron, …) → `BIOTECH_PHARMA` + tag `"big pharma"`

   Emits a `ClassificationHint`, **not** a final label. It exists to (a) raise LLM accuracy as a prompt hint and (b) be the **complete fallback when no `OPENAI_API_KEY` is set**, so the whole pipeline is testable offline.

2. **Batched LLM structured pass** (`classify.py`) — batches of 25, `output_type=ClassificationBatch`. At 100–300 people that's 4–12 calls per event, once ever. (Per-person = 300 calls; one giant call risks truncation and label drift late in the list.) Prompt carries forward the existing guardrail at `agent.py:268`: label from the given text only, prefer `other`/`unknown` over guessing, never infer gender/ethnicity/age or any protected attribute.

```python
class PersonLabels(BaseModel):
    person_ref: str                      # opaque index the batch caller maps back
    field: FieldCategory
    field_other_label: str | None = None
    role_type: RoleType
    seniority: Seniority
    industries: list[Industry] = Field(default_factory=list, max_length=3)
    tags: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0, le=1)

class ClassificationBatch(BaseModel):
    labels: list[PersonLabels]
```

3. **Cache** on `input_fingerprint = sha256(f"{TAXONOMY_VERSION}|{name}|{headline}|{company}|{bio_short}")`. Filter already-classified people out before batching. Bumping `TAXONOMY_VERSION` re-classifies everyone with no migration.

**Rollup** (`snapshot.py`) → `RoomSnapshot{sections, basis, generated_at}`:

```python
class SnapshotBar(BaseModel):    key: str; label: str; count: int; percentage: int
class SnapshotSection(BaseModel):
    id: Literal["fields","role_types","seniority","industries"]
    title: str                   # "Most common fields" / "Professional roles" / "Seniority"
    bars: list[SnapshotBar]
class SnapshotBasis(BaseModel):
    registered_count: int | None # Luma guest_count, e.g. 552
    confirmed_people: int        # hosts + featured guests + speakers, e.g. 12
    inferred_people: int         # SERP-discovered
    classified_people: int       # denominator for every percentage
    disclaimer: str
```

- Percentages use **largest-remainder (Hare-Niemeyer)** so each section sums to exactly 100 — the screenshot's do (18+17+16+12+10+6+21, 33+52+15) and naive rounding gives 99/101 and looks broken.
- Bars sort descending by count, except `other`/`unknown` **pinned last regardless of size** (matches the screenshot's trailing `other 21%`).
- Screen 1's category strip = `sections[0].bars[:3]`, same function.
- `basis.disclaimer` is generated server-side, not hardcoded in the UI: *"Estimated from 12 confirmed and 84 inferred public profiles. 552 people are registered; Luma does not publish the guest list."*

## 4. Chat — `src/invite_finder/chat.py`

**Structured-filter interpretation, not RAG.** At 100–300 people the whole roster is only 4–12k tokens, so context-stuffing is affordable — but it's re-sent every turn, gives no explainability, can't paginate ("show me 20 more"), and isn't reproducible. Embeddings are worse: a 300-item corpus doesn't need ANN, and "show me VCs" is a *categorical* question the labels already answer exactly.

Per turn:
1. **Interpret** — one structured call → `PersonFilter{fields, role_types, seniorities, industries, tags_any, company_keywords, headline_keywords, exclude_keywords, confirmed_only, limit, interpretation}`. Prompt includes the taxonomy enums *and the top ~40 tags actually present at this event*, so it filters on things that exist.
2. **Filter + rank deterministically** in `people_store.query_people(conn, event_id, filter)` — SQL `WHERE` over `person_classifications ⋈ event_people`, `LIKE` on headline/company/bio, JSON1 `EXISTS` over `industries_json`/`tags_json`. Rank: confirmed first, then `relevance_score DESC`, then label `confidence DESC`. Cap at `limit` (default 8, max 20).
3. **Fallback when `len(matches) < 3`** — send the compact roster (`id | name | headline | company | field/role/seniority/industries`) for the whole event and ask for `list[int]` of person ids plus a reason. Bounded ~12k tokens, miss path only.
4. **Write highlights** — one call over just the shortlist → `ChatAnswer` with per-person `highlight` (1–2 sentences) and `why_relevant` (one clause tied to the actual query). Instructed to use only supplied fields, never invent employers or titles, and to say when evidence is thin.

Response carries structured cards so the UI renders cards, not markdown:

```python
class PersonCard(BaseModel):
    person_id: int; name: str | None; headline: str | None; company: str | None
    linkedin_url: str | None; avatar_url: str | None
    highlight: str; why_relevant: str
    is_confirmed_attendee: bool
    relation: Literal["host","featured_guest","speaker","inferred"]
    labels: PersonLabelsView; relevance_score: int | None; evidence: list[str] = []

class ChatQueryResponse(BaseModel):
    thread_id: int; message_id: int
    reply: str                    # 1-2 sentence framing, NOT a list of people
    interpreted_filters: PersonFilter
    cards: list[PersonCard]
    total_matches: int; used_fallback: bool; caveats: list[str] = []
```

## 5. HTTP API — `src/invite_finder/api/`

New files: `api/{__init__,app,deps,schemas,routes_events,routes_runs,routes_chat}.py`.

Add `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `sse-starlette>=2.1` to `pyproject.toml` (the latter two are already resolved in `.venv`; only fastapi will actually install). Add `[project.scripts] invite-api = "invite_finder.api.app:main"`.

New **optional** env vars in `config.py` (keeps `Settings` frozen and backwards compatible): `INVITE_DB_PATH` (default `data/private/invite_finder.db`), `INVITE_OFFLINE` (default `0`), `INVITE_CORS_ORIGINS` (default `http://localhost:3000`). **Also make the three `BRIGHTDATA_*` vars non-required when `INVITE_OFFLINE=1`** — otherwise `Settings.from_env()` (`config.py:38`) raises `ConfigError` and offline dev is impossible. Add `Settings.from_env(offline: bool | None = None)`.

| Method | Path | → | Codes |
|---|---|---|---|
| GET | `/api/health` | `{status, db, offline}` | 200 |
| GET | `/api/events?from=&to=&limit=&offset=` | `{events: [EventSummary], total}` | 200 |
| POST | `/api/events` | `{luma_url, force_refresh, max_profiles}` → `{run_id, event_id, status, already_cached}` | **202** new run · **200** `{event_id, status:"ready"}` cached · 400 not Luma · 409 run active for slug · 503 missing config |
| GET | `/api/events/{id}` | `EventDetail{event, snapshot, counts, sources, last_run}` | 200 · 404 |
| GET | `/api/events/{id}/people?field=&role_type=&seniority=&industry=&confirmed=&q=&limit=&offset=` | `{people: [PersonCard], total}` | 200 · 404 |
| GET | `/api/runs/{id}` | `RunStatus{id, event_id, status, phase, stats, error, events[-50:]}` | 200 · 404 |
| GET | `/api/runs/{id}/stream?after_seq=0` | SSE `text/event-stream` | 200 · 404 |
| POST | `/api/events/{id}/chat` | `{message, thread_id}` → `ChatQueryResponse` | 200 · 404 · 409 not ready · 502 |
| GET | `/api/events/{id}/chat/{thread_id}` | `{messages}` | 200 · 404 |
| DELETE | `/api/events/{id}` | cascades everywhere (PII deletion) | 204 · 404 |

`EventSummary` (Screen 1 card): `{id, slug, name, cover_url, start_at, end_at, timezone, venue_name, city, guest_count, people_analyzed, top_fields: [SnapshotBar], status}`.

Errors use one shape `{"error": {code, message, detail}}` via exception handlers: `LumaUrlError`→400, `BrightDataError`→502, `CacheMiss`→503, `ConfigError`→503. CORS middleware allowing `INVITE_CORS_ORIGINS`, credentials off.

**Run execution** (`src/invite_finder/runner.py`): job table + in-process `asyncio.create_task` — **not** `BackgroundTasks` (no handle, no cancellation, no status). `RunManager` holds `dict[int, asyncio.Task]`. `execute_run` opens its **own** sqlite connection (connections aren't safe to share across tasks). Every state change writes to `runs` + `run_events`; **nothing about progress lives only in memory**, so a Fly restart leaves an honest `running` row that a boot-time reaper flips to `failed` (`UPDATE runs SET status='failed' WHERE status IN ('queued','running')`).

**SSE polls `run_events`** (`WHERE run_id=? AND seq>? ORDER BY seq`, 400 ms tick, heartbeat comment every 15 s, terminate on `done`/`error`). No in-memory pub/sub — restart-safe and reconnect-safe. Requires `uvicorn --workers 1`; document in `fly.toml`. Frame: `event: <type>` / `data: {seq, ts, phase, message, data}`.

**Progress emission without editing the three `@function_tool`s** — three sources:
1. `ObservedBrightDataClient` wrapping `LeadFinderContext.brightdata` → a `serp`/`fetch`/`cache_hit` event per call. This is the meaty progress.
2. `Runner.run_streamed` in the orchestrator — iterate `result.stream_events()`; `RunItemStreamEvent` with `name in ("tool_called","tool_output")` → coarse `log`, `reasoning_item_created` → thinking tick.
3. **Zero-touch safety net:** diff `context.serp_queries_used` / `context.fetched_pages` (already accumulated at `agent.py:32-33`) against last emitted length and emit the delta. Survives SDK event-name drift.

`RunPhase = queued | luma_fetch | seeding | serp_discovery | profile_enrichment | classification | snapshot | done | failed`.

**Pipeline** (`src/invite_finder/pipeline.py`) — `run_event_pipeline(conn, client, reporter, luma_url, params) -> int`:
1. `luma_fetch` → `fetch_luma_event` → upsert `events`
2. `seeding` → hosts/featured guests/speakers into `people` + `event_people` with `is_confirmed=1`. **These 12 people are also the best SERP seeds** — replace the hardcoded `"vla"/"robot"` branch in `build_seed_search_queries` (`agent.py:98-147`) with a version deriving topics from `event.name`, `event.categories`, `event.description`, and confirmed guests' companies/headlines. Keep a two-arg shim, or deliberately update `tests/test_agent_helpers.py`.
3. `serp_discovery` → existing agent via `Runner.run_streamed`, city from `event.venue.city` → upsert `ProfileSearchReport.candidates` with `is_confirmed=0`
4. `profile_enrichment` → budgeted `unlock_url` on top scorers (cached ⇒ free on re-run)
5. `classification` → rules + batched LLM → `person_classifications`
6. `snapshot` → `build_room_snapshot`
7. `done`

## 6. Frontend — `invite_viewer/app/`

**Before writing any component:**
1. `cd invite_viewer && npm install` — `node_modules` is absent, so nothing compiles *and the Next 16 docs aren't on disk*.
2. Per `invite_viewer/AGENTS.md`, read `node_modules/next/dist/docs/`. Specifically confirm for **16.2.6**: (a) whether `params`/`searchParams` in `page.tsx` are Promises requiring `await`, (b) the current `next/image` remote-host config key, (c) server-component `fetch` caching defaults, (d) whether `export const dynamic = "force-dynamic"` is still the right escape hatch (it's used at `app/dashboard/page.tsx`).
3. `next.config.ts` needs remote image permission for `images.lumacdn.com` (covers and avatars). LinkedIn avatars are not fetched, so no other hosts.

| Route | |
|---|---|
| `app/page.tsx` | **keep** — only change the three `/dashboard` links (~96, ~182, ~205) → `/events` |
| `app/events/page.tsx` | **new**, server — Screen 1: `await listEvents({from,to})`, date nav + cards + add-event form |
| `app/events/[slug]/page.tsx` | **new**, server — Screen 2: `await getEvent(slug)`; if `status !== "ready"` render `<RunProgress>` instead of the snapshot |
| `app/dashboard/page.tsx` | **delete** |

**Delete:** `app/components/InviteViewer.tsx` (705 lines, report-file oriented), `app/lib/reports.ts` (reads `process.cwd()/../reports` — fundamentally incompatible with a deployed API-backed app; there is no `reports/` dir on Fly).

**Replace keeping the pattern:** `app/lib/report-shape.ts` → `app/lib/api-shape.ts`. Keep its exact defensive idiom (`asStringArray`, per-field `typeof` guards, never trust the payload) extended to `normalizeEventSummary`, `normalizeEventDetail`, `normalizeRoomSnapshot`, `normalizePersonCard`, `normalizeChatResponse`, `normalizeRunEvent`. This matters *more* now — payloads come from a network service that can be mid-deploy or returning an error envelope.

**New:**

| File | Kind | Responsibility |
|---|---|---|
| `lib/api.ts` | shared | `apiBase()` (server `INVITE_API_BASE_URL` / client `NEXT_PUBLIC_INVITE_API_BASE_URL`, default `http://localhost:8000`), `listEvents`, `getEvent`, `getPeople`, `startRun`, `postChat` — fetch + normalize + typed return, `cache: "no-store"` |
| `lib/format.ts` | shared | `formatEventTime(startAt, timezone)` via `Intl.DateTimeFormat` with the event's IANA tz (Luma gives `America/Los_Angeles`), `formatVenue` |
| `components/events/EventDateNav.tsx` | client | date navigator; pushes `?from=&to=` via `useRouter` |
| `components/events/EventCard.tsx` | server | cover, time, venue, `~N people`, `<CategoryStrip>` |
| `components/events/CategoryStrip.tsx` | server | top-3 field bars as inline percentage chips |
| `components/events/AddEventForm.tsx` | client | Luma URL → `POST /api/events` → route to `/events/[slug]`, or render `<RunProgress runId>` inline on 202 |
| `components/event/EventHeader.tsx` | server | cover, title, date/time, venue, `~N registered`, link to Luma |
| `components/event/RoomSnapshot.tsx` | server | sections + `<PercentBar>` rows; renders `basis.disclaimer` as **visible** small print, not a tooltip |
| `components/event/PercentBar.tsx` | server | label, bar (width `%`), percentage. `#3d7ffc` on `#dbe8ff` track |
| `components/event/RunProgress.tsx` | client | `EventSource(.../stream?after_seq=)`, appends log lines, tracks phase, `router.refresh()` on `done`, reconnects with last `seq`, falls back to polling `GET /api/runs/{id}` after 3 failed reconnects |
| `components/event/ConnectorChat.tsx` | client | "Your Event Super Connector" — greeting + suggested prompts ("show me potential VCs", "people from big pharma", "founders raising"), `POST /chat`, renders `reply` + `<PersonCardList>`; messages in `useState`, hydrated from `GET /chat/{thread_id}` |
| `components/event/PersonCardList.tsx` | client | maps `cards` → `<PersonCard>` |
| `components/event/PersonCard.tsx` | client | avatar, name, headline, company, `highlight`, `why_relevant`, LinkedIn link (`target="_blank" rel="noreferrer"`), and the **"Confirmed guest" vs "Likely relevant" pill** driven by `is_confirmed_attendee` — the honesty requirement rendered in UI |
| `components/ui/Pill.tsx`, `ui/Skeleton.tsx` | shared | primitives, palette `#091b36 / #3d7ffc / #f4f7fb` |

Event list/detail are **server** fetches (keeps the API base URL server-side, no loading flash); chat and run progress are **client** fetches (need `EventSource` + interaction). Document both env vars in `invite_viewer/.env.local.example`.

Screen 2 layout: `grid lg:grid-cols-[minmax(320px,0.9fr)_minmax(0,1.6fr)]` — snapshot left, chat right; stacked on mobile with **chat first** (it's the product).

---

## 7. Phases

Phases 0–8 need **no live Bright Data calls** — the `.env` zones are currently invalid (the one existing report's caveats confirm both 404'd).

0. **Fixtures + deps** — add the three deps; capture `tests/fixtures/{luma_api_vla_night_panel.json, serp_linkedin_sample.json, linkedin_profile_sample.md}`. *Verify:* existing 4 test files still green.
1. **Storage** — `db.py` + `store/*`. *Verify:* `tests/test_db.py` — migrations apply idempotently to `:memory:`, round-trip an event/person/classification, `schema_version` advances once.
2. **Cache** — `cache.py` + `fingerprint()`. *Verify:* `tests/test_cache.py` — with a stubbed inner transport, a second identical `unlock_url` makes **zero** network calls; zone rename doesn't change the fingerprint; tracking params stripped; `offline=True` + empty cache raises `CacheMiss`.
3. **Luma ingestion** — *Verify:* `tests/test_luma.py` — fixture parses to `guest_count == 552`, `venue.city == "San Francisco"`, `len(people) == 12`, `/company/bright-data` lands in `linkedin_company_url` **not** `linkedin_url`; JSON-LD fallback test; garbage-HTML test yields `ingest_source="luma_markdown"` + warnings without raising.
4. **Taxonomy + classification + snapshot** — *Verify:* `test_classify_rules.py` headline→label table ("Partner at Foo Ventures"→VC, "Principal Scientist, Genentech"→biotech_pharma + tag "big pharma"); `test_snapshot.py` — every section sums to exactly 100 for adversarial counts (7 items of 1/7, single item, zero items), `other` sorts last.
5. **Pipeline + runs** — `runner.py`, `pipeline.py`, `observability.py`. *Verify:* `test_pipeline.py` runs the full pipeline `offline=True` against the seeded fixture cache with a stub agent → `events` row, ≥12 `event_people`, classifications present, monotonic `run_events` ending in `done`.
6. **API** — *Verify:* `test_api.py` with `TestClient` on a temp DB; every endpoint's status code; `POST /api/events` twice returns 202 then 200-cached; SSE yields the seeded events and terminates.
7. **Chat** — *Verify:* `test_chat.py` with a stubbed interpret call; "show me potential VCs" → filter with `industries=[vc_investor]` → the seeded VC returned, the non-VC not; nonsense query sets `used_fallback=True`.
8. **Frontend** — `npm install`, read the Next 16 docs, then build in order: `api.ts`/`api-shape.ts` → `/events` → `/events/[slug]` snapshot → `RunProgress` → `ConnectorChat`. **Delete `InviteViewer.tsx`/`reports.ts`/`dashboard/` last**, after `/events` renders, so there's always a working page. *Verify:* `npm run build` and `npm run lint` clean; both screens render against the seeded DB.
9. **Live + deploy** — fix the Bright Data zone names, run one real cold event end-to-end, then `Dockerfile` + `fly.toml` with a 1 GB volume at `/data`, `INVITE_DB_PATH=/data/invite_finder.db`, `uvicorn --workers 1`.

**Offline dev tooling:** `python -m invite_finder.tools.seed_cache --fixtures tests/fixtures --db data/private/invite_finder.db` inserts fixture bodies under the *exact fingerprints the real code computes* — the seeder **imports `fingerprint()`** rather than hardcoding hashes, otherwise it drifts silently. Then `python -m invite_finder.tools.seed_demo` runs the pipeline offline and leaves a demo event in the DB for frontend work.

## 8. Verification

New pytest files: `test_db`, `test_cache`, `test_luma`, `test_classify_rules`, `test_snapshot`, `test_pipeline`, `test_api`, `test_chat`, plus `tests/conftest.py` with `tmp_db` / `seeded_cache` / `fake_settings` fixtures. **No LLM or network calls in any test** — stub at the `classify_batch()` / `interpret_query()` seam, not at the OpenAI SDK level. The existing 4 test files stay green; the only one at risk is `test_agent_helpers.py::test_seed_queries_for_vla_start_broad_before_exact_vla` if `build_seed_search_queries` changes signature.

```bash
# terminal 1 — backend
cd /Users/sai/Projects/GTM-For-Events
.venv/bin/pip install -e ".[dev]"
INVITE_OFFLINE=1 .venv/bin/python -m invite_finder.tools.seed_cache --fixtures tests/fixtures
INVITE_OFFLINE=1 .venv/bin/python -m invite_finder.tools.seed_demo
INVITE_OFFLINE=1 .venv/bin/uvicorn invite_finder.api.app:app --reload --port 8000 --workers 1

# terminal 2 — frontend
cd /Users/sai/Projects/GTM-For-Events/invite_viewer
npm install
NEXT_PUBLIC_INVITE_API_BASE_URL=http://localhost:8000 INVITE_API_BASE_URL=http://localhost:8000 npm run dev

# tests
.venv/bin/pytest
cd invite_viewer && npm run lint && npm run build

# smoke
curl -s localhost:8000/api/health
curl -s -X POST localhost:8000/api/events -H 'content-type: application/json' \
  -d '{"luma_url":"https://luma.com/vla-night-panel"}'
curl -N localhost:8000/api/runs/1/stream
```

## 9. Risks

1. **Snapshot honesty is the product risk** — 12 real people out of 552 registered. The percentages describe the *inferred* room, not the actual room. Mitigation is structural, not cosmetic: `SnapshotBasis` required, `disclaimer` server-generated, confirmed/likely pill on every card. Don't let a later polish pass drop these.
2. **`api.lu.ma` is undocumented** and may change or start blocking (`get-guest-list` already 401s; `/url` could follow). Mitigations: always go through Web Unlocker (never direct), three-tier fallback, store `raw_json` so parsing can be re-run without re-fetching, surface `ingest_warnings` in the UI.
3. **`data_format="markdown"` on a JSON endpoint silently corrupts the payload** — easy to hit since markdown is the default. Assert the body parses as JSON before trusting it; fall through to tier 2 if not.
4. **SERP yield is unproven** — the one existing report produced *zero* candidates, `site:linkedin.com/in/` results are thin, and Google increasingly suppresses them. Design the UI for an event with 15 people, not 300. Seeding queries from confirmed guests' companies and headlines should improve yield materially over the current hardcoded robotics branch, but that's untested until phase 9. If yield stays low, the documented upgrade path is Bright Data's LinkedIn dataset API, already named in `docs/bright-data-gtm-my-events-proposal.md:172`.
5. **Classification quality on one-line headlines** — "Building something new" tells you nothing. Expect a meaningful `other`/`unknown` bucket; that's why the taxonomy has both, and the screenshot's own `other 21%` suggests it reads as normal rather than broken.
6. **Single-writer SQLite + `--workers 1`** is the scaling ceiling. Fine for a demo; the migration path is Postgres + `LISTEN/NOTIFY` for SSE, which the `run_events` table design already anticipates.
7. **PII** — the DB holds names and LinkedIn URLs. Gitignored locally, but a Fly volume isn't encrypted at rest by default. Hence `DELETE /api/events/{id}` lands in phase 6, not "later".

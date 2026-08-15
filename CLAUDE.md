# Luma Connects

Event intelligence app: paste a Luma event link, get a room breakdown (field
/ role / seniority) and a chat assistant that answers "who should I meet"
with LinkedIn-linked person cards. Built on Bright Data (event research +
LinkedIn discovery via SERP) and OpenAI (classification + chat).

Full operational docs: [`docs/RUNBOOK.md`](docs/RUNBOOK.md). Product and
architecture rationale for the current design: [`docs/event-room-snapshot-plan.md`](docs/event-room-snapshot-plan.md).
Repo: https://github.com/anhsirksai/luma-connects (private).

## Two services, run together

- **Backend** — FastAPI, `src/invite_finder/api/app.py`. Single SQLite file
  at `data/private/invite_finder.db` (gitignored — contains names/LinkedIn
  URLs).
- **Frontend** — Next.js 16, `invite_viewer/`. **Read `invite_viewer/AGENTS.md`
  before touching Next.js APIs** — this app pins a version with breaking
  changes vs. training data.

```bash
.venv/bin/uvicorn invite_finder.api.app:app --port 8000   # terminal 1 — auto-loads .env
cd invite_viewer && npm run dev                             # terminal 2
```

Open `http://localhost:3000/events`. No Bright Data/OpenAI credentials yet?
See `docs/RUNBOOK.md` for offline mode (cache-seeded, costs nothing) — or
use the `run-app` project skill (`.claude/skills/run-app/`), which covers
both modes with exact commands.

## Architecture

```
Luma URL -> src/invite_finder/luma.py       (3-tier: api.lu.ma JSON -> JSON-LD -> markdown fallback)
         -> src/invite_finder/pipeline.py   (orchestrates: fetch -> seed confirmed guests -> SERP discovery -> classify -> snapshot)
         -> src/invite_finder/agent.py      (OpenAI Agents SDK, SERP discovery via Bright Data)
         -> src/invite_finder/classify.py   (rules pass + batched LLM -> taxonomy.py)
         -> src/invite_finder/snapshot.py   (rollup into Room Snapshot percentages)
         -> SQLite (src/invite_finder/db.py) <- src/invite_finder/api/* (FastAPI) <- invite_viewer/ (Next.js)
```

Every Bright Data call passes through `src/invite_finder/cache.py`
(`CachingSession`) before it reaches the network. Repeat runs against the
same URL never re-hit Bright Data. This caching is a hard product
requirement, not an optimization — don't bypass or weaken it.

## Key modules

- `store/` — one file per table (`event_store`, `people_store`, `run_store`,
  `chat_store`), every function takes `conn` as the first arg
- `luma.py` / `luma_models.py` — Luma ingestion, three-tier fallback
- `taxonomy.py` — classification enums: `FieldCategory`, `RoleType`,
  `Seniority`, `Industry`
- `classify.py` / `classify_rules.py` — LLM + rules-fallback classification;
  the test seam is `classify_batch()`
- `chat.py` — structured-filter chat, not RAG; test seams are
  `interpret_query()`, `fallback_pick()`, `write_highlights()`
- `pipeline.py` — orchestrates one run end-to-end; `compute_snapshot_for_event()`
  is cheap (SQL only) and safe to call on every GET
- `runner.py` — `RunManager` tracks in-process asyncio tasks for background
  runs; progress lives in the `runs`/`run_events` tables, not in memory
- `api/` — FastAPI routes. One error envelope everywhere:
  `{"error": {code, message, detail}}`, including plain `HTTPException`
  (normalized by a global handler in `api/app.py`)

## Testing

```bash
.venv/bin/pytest                                    # 143 tests, zero network/LLM calls
cd invite_viewer && npm run lint && npm run build
```

No test hits Bright Data, OpenAI, or the network. LLM calls are stubbed at
the function boundary (`classify_batch`, `interpret_query`, `fallback_pick`,
`write_highlights`, and the SERP discovery `Runner.run`) — see
`tests/test_pipeline.py`, `tests/test_chat.py`, `tests/test_api.py` for the
pattern before adding new LLM-calling code.

## Gotchas already hit once — don't reintroduce

- **Ports**: README, RUNBOOK, and `invite_viewer/.env.local.example` are all
  standardized on backend port `8000`. If you change it, update all three —
  a mismatch fails silently with "Could not reach the API" at the wrong port.
- **`.env` loading**: `Settings.from_env()` in `config.py` calls
  `load_dotenv_if_available()` itself. Don't remove that — it's the only way
  the API server (as opposed to the CLI) picks up credentials; there's no
  `main()` entry point when uvicorn imports `invite_finder.api.app:app` by
  string reference.
- **Sync routes + `asyncio.create_task`**: FastAPI runs sync (`def`) routes
  in a worker thread pool; `asyncio.create_task()` needs the main event loop
  thread. `create_event` in `api/routes_events.py` is `async def` for
  exactly this reason — making it sync again reintroduces
  `RuntimeError: no running event loop`.
- **SQLite cross-thread**: connections are opened with
  `check_same_thread=False` in `db.py` deliberately, because FastAPI can
  resolve a sync dependency in a worker thread and use it from an async
  endpoint.
- **`data_format="markdown"` on JSON endpoints**: `unlock_url`'s default
  `data_format` is `"markdown"`, which corrupts JSON responses (e.g.
  `api.lu.ma`). Always pass `data_format=None` explicitly for JSON targets.

## Status

Fully built, tested (143 backend tests, frontend lint/build clean), and
browser-verified end-to-end against real OpenAI credentials. **Deploy target is
Render** (`render.yaml` Blueprint, same root `Dockerfile`); Fly configs are
still present but that account's free allowance is exhausted. The service must
stay always-on with a stable public URL — it receives Linq and Stripe webhooks,
so scale-to-zero and idle-spindown both drop payments. Historical planning docs (`docs/bright-data-gtm-my-events-proposal.md`,
`docs/event-room-snapshot-plan.md`) predate the "Luma Connects" rename and
still say "GTM My Events" — that's intentional, they're records of what was
proposed/planned at the time, not live branding.

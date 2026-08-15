# Runbook

Operational guide for running, testing, and deploying Luma Connects. For the
product/architecture rationale, see [`event-room-snapshot-plan.md`](./event-room-snapshot-plan.md).

## Architecture at a glance

```
Luma URL -> src/invite_finder/luma.py       (api.lu.ma JSON -> JSON-LD -> markdown fallback)
         -> src/invite_finder/pipeline.py   (orchestrates the run, phase by phase)
         -> src/invite_finder/agent.py      (existing OpenAI Agents SDK SERP discovery)
         -> src/invite_finder/classify.py   (rules pass + batched LLM classification)
         -> src/invite_finder/snapshot.py   (rollup into Room Snapshot percentages)
         -> SQLite (src/invite_finder/db.py) <- src/invite_finder/api/*  (FastAPI)  <- invite_viewer/ (Next.js)
              ^
         every Bright Data call passes through src/invite_finder/cache.py first
```

Two services, run separately:

- **Backend** — FastAPI app at `src/invite_finder/api/app.py`, backed by a single
  SQLite file. Serves the REST API and an SSE endpoint for live run progress.
- **Frontend** — Next.js app in `invite_viewer/`. Talks to the backend over
  HTTP; never touches SQLite or Bright Data directly.

## Prerequisites

- Python >= 3.9 (developed against 3.14 in `.venv`)
- Node.js 20+ and npm
- A Bright Data account with a **Web Unlocker** zone and a **SERP API** zone
  (only required for live runs — offline/dev mode does not need these)
- An OpenAI API key (only required for live SERP discovery and LLM
  classification — offline/dev mode does not need this either)

## First-time setup

```bash
# backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in real values for a live run; see below for offline mode

# frontend
cd invite_viewer
npm install
cp .env.local.example .env.local
cd ..
```

## Running locally

### Offline / demo mode (no Bright Data or OpenAI spend)

Useful for frontend work, or to sanity-check the stack without touching any
paid API. `INVITE_OFFLINE=1` makes `Settings.from_env()` skip the Bright Data
zone requirement and makes the cache refuse any network call it hasn't
already seen — everything runs against `http_cache` only.

```bash
# terminal 1 — backend
INVITE_OFFLINE=1 BRIGHTDATA_API_KEY=x BRIGHTDATA_SERP_ZONE=x BRIGHTDATA_UNLOCKER_ZONE=x \
  .venv/bin/uvicorn invite_finder.api.app:app --port 8000 --reload

# terminal 2 — frontend
cd invite_viewer
INVITE_API_BASE_URL=http://localhost:8000 NEXT_PUBLIC_INVITE_API_BASE_URL=http://localhost:8000 \
  npm run dev
```

Open `http://localhost:3000/events`. With an empty cache, submitting a Luma
URL will fail fast with a `CacheMiss` (503) — offline mode has nothing to
serve. To see real data without spending anything, seed the cache from the
committed fixture first:

```python
# from the repo root, .venv/bin/python
import json
from invite_finder import db
from invite_finder.cache import seed_from_fixture

conn = db.connect("data/private/invite_finder.db")
fixture_text = open("tests/fixtures/luma_api_vla_night_panel.json").read()
seed_from_fixture(
    conn,
    payload={"url": "https://api.lu.ma/url?url=vla-night-panel", "format": "raw", "method": "GET"},
    body=json.dumps({"body": fixture_text}),
)
```

Then POST `https://luma.com/vla-night-panel` from the "Add an event" form.
Luma ingestion (12 confirmed guests) will succeed from cache; SERP discovery
and classification will still need a real `OPENAI_API_KEY` to go further —
without one, the run fails cleanly at the `serp_discovery` phase and the
confirmed guests remain visible on the event page (nothing is lost).

### Live mode (spends Bright Data + OpenAI credits)

```bash
# terminal 1 — backend; Settings.from_env() loads .env automatically
.venv/bin/uvicorn invite_finder.api.app:app --port 8000 --reload

# terminal 2 — frontend (same as above)
cd invite_viewer && npm run dev
```

Every Bright Data response is cached in SQLite the moment it's fetched —
re-analyzing the same event, or restarting either server, never re-issues a
request that's already been made. Cost only grows with the number of
*distinct* Luma URLs you submit, and each run's `--max-profiles` /
`--max-serp-queries` budget (set per-request, defaults are conservative).

### Force-refreshing an already-analyzed event

`POST /api/events` with the same `luma_url` a second time is a no-op by
default — `routes_events.py` sees the event already exists and returns the
cached result (`200`, `already_cached: true`) without touching the pipeline
at all. To force a real re-run:

```bash
curl -X POST http://localhost:8000/api/events \
  -H 'content-type: application/json' \
  -d '{"luma_url": "https://luma.com/<slug>", "force_refresh": true}'
```

`force_refresh` flows through `routes_events.py` -> `build_client_for_run()`
(via `functools.partial`, see `api/deps.py`) -> `CachingSession(...,
force_refresh=True)`. Per `cache.py`, that means every Bright Data call in
the run **bypasses the cache read but still writes** its result — so the
Luma event fetch, every SERP query, and every profile-page fetch in that run
get fresh responses, each of which also refreshes that fingerprint's cached
copy. Expect real Bright Data (and OpenAI, for classification/discovery)
spend proportional to the run's budgets, not just a cache-bust of one URL.

There's no UI control for this yet — the "add event" form in
`invite_viewer/app/components/events/AddEventForm.tsx` always sends
`force_refresh: false` implicitly (the default in `lib/api.ts`'s
`startRun()`). Use cases: picking up a parser/classification bug fix on an
event analyzed before the fix, or getting current data for an event whose
Bright Data cache entries (Luma: 6h, SERP/profiles: 30d — see
`cache.TTL_BY_KIND`) haven't expired but you want anyway.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | live mode | SERP discovery agent + classification + chat |
| `OPENAI_AGENT_MODEL` | no (default `gpt-5.5`) | model used everywhere |
| `BRIGHTDATA_API_KEY` | live mode | Bright Data auth |
| `BRIGHTDATA_SERP_ZONE` | live mode | SERP API zone name |
| `BRIGHTDATA_UNLOCKER_ZONE` | live mode | Web Unlocker zone name |
| `INVITE_DB_PATH` | no (default `data/private/invite_finder.db`) | SQLite file location |
| `INVITE_OFFLINE` | no (default `0`) | `1` = cache-only, skips Bright Data zone validation |
| `INVITE_CORS_ORIGINS` | no (default `http://localhost:3000`) | comma-separated allowed origins |
| `INVITE_API_BASE_URL` | frontend, server-side | backend URL for Next.js server components |
| `NEXT_PUBLIC_INVITE_API_BASE_URL` | frontend, client-side | backend URL baked into the browser bundle at build time |
| `LINQ_API_KEY` | messaging | Linq Partner API auth; blank = webhook parses but sends no reply |
| `LINQ_WEBHOOK_SECRET` | messaging | Standard Webhooks secret; blank disables verification (dev only) |
| `LINQ_API_BASE_URL` | no (default `https://api.linqapp.com/api/partner/v3`) | Linq API root |
| `STRIPE_PAYMENT_LINK` | payments | the single Payment Link; order rides on `?client_reference_id=` |
| `STRIPE_WEBHOOK_SECRET` | payments | endpoint signing secret; blank disables verification (dev only) |
| `APIFY_TOKEN` | enrichment | channel B without KYC — profile search, posts |
| `PERFLO_AGENT_TOKEN` | enrichment | agent token for the Perflo service marketplace |
| `PERFLO_MANDATE_ID` | enrichment | the budget-capped mandate purchases are billed to |
| `PERFLO_API_BASE_URL` | no (default `https://api.perflo.ai`) | Perflo API root |
| `ENRICHMENT_BUDGET_CENTS` | no (default `500`) | hard spend ceiling per process, independent of the mandate |
| `PUBLIC_BASE_URL` | messaging | public HTTPS base for webhook callbacks (ngrok/cloudflared in dev) |
| `ADMIN_PHONE` | **before any public URL** | turns on the passcode gate; the code is texted here. Blank = API is open |
| `ADMIN_SESSION_TTL_HOURS` | no (default `24`) | how long an admin session lasts |

### Operator auth

Tunnels and Superserve preview URLs are unauthenticated by design — anyone who
learns the URL can reach it — and the data routes serve names, LinkedIn URLs,
emails and phone numbers. Setting `ADMIN_PHONE` gates them behind a one-time
passcode texted to that number:

```bash
# 1. ask for a code (it arrives by text)
curl -X POST https://<host>/api/auth/request-code

# 2. exchange it for a 24h session token
curl -X POST https://<host>/api/auth/verify \
  -H 'content-type: application/json' -d '{"code":"123456"}'

# 3. use it
curl https://<host>/api/events -H "Authorization: Bearer <token>"
```

Codes are single-use, expire in 10 minutes, allow 5 attempts, and requesting a
new one invalidates the old. Only salted hashes are stored, so a copy of the
database yields neither a working code nor a live session.

**What is deliberately *not* gated:** `/api/webhooks/*` (Stripe and Linq call
machine-to-machine and cannot present a passcode — they authenticate by
signature instead, verified in each route), `/api/auth/*` (how you get in), and
`/api/health`. Gating the webhooks would silently stop every payment.

**Leaving `ADMIN_PHONE` blank leaves the API open.** That is intentional for
localhost — with no phone there is no way to deliver a passcode, so enforcing
it would lock you out of your own API — but startup prints a warning and
`/api/health` reports `"admin_auth": "off"`.

> ⚠️ The Next.js frontend in `invite_viewer/` does **not** yet send an admin
> token. With `ADMIN_PHONE` set, the backend returns 401 to it. Either leave
> the gate off while working on the frontend locally, or add the token to
> `lib/api.ts` — the backend accepts `Authorization: Bearer <token>` or
> `X-Admin-Token: <token>`.

## Testing

```bash
.venv/bin/pytest                      # backend: 143 tests, no network/LLM calls
cd invite_viewer && npm run lint && npm run build   # frontend
```

No test hits Bright Data, OpenAI, or the network — the pipeline's LLM calls
(`classify_batch`, `interpret_query`, `fallback_pick`, `write_highlights`,
and the SERP discovery `Runner.run`) are stubbed at the function boundary in
`tests/test_pipeline.py`, `tests/test_chat.py`, and `tests/test_api.py`.

## Deploying

Two supported targets. **Render is the current one** — Fly's free allowance
ran out, and Render gives the same thing (Docker web service + persistent
disk) with hackathon credits behind it.

Whichever you pick, the requirement is the same and it is not negotiable: a
service that stays up, on a stable public HTTPS URL. Linq and Stripe push
webhooks *to* this app, so anything that sleeps when idle drops payments.

> **Not Superserve or sandbox0.** Both are agent *sandboxes* — isolated VMs an
> agent gets to execute code in, designed to pause between turns and resume.
> That is the opposite of a webhook listener. They would suit a future split
> where enrichment runs as a detached worker; they cannot host this API.

### Render (current)

`render.yaml` at the repo root is a Blueprint: Render dashboard -> New ->
Blueprint -> point it at this repo. It builds the same root `Dockerfile`, so
nothing about the image changes.

```bash
# 1. Claim the hackathon credits first
#    https://credits-portal-mmdm.onrender.com/claim/terac-hackathon
# 2. New -> Blueprint -> select this repo -> Apply
# 3. Render prompts for every `sync: false` secret in render.yaml
#    (OPENAI_API_KEY, LINQ_*, STRIPE_*, APIFY_TOKEN, PERFLO_*)
# 4. Point the webhooks at the deployed URL:
#      Linq   -> https://<service>.onrender.com/api/webhooks/linq
#      Stripe -> https://<service>.onrender.com/api/webhooks/stripe
```

> ⚠️ **The blueprint is currently on `plan: free`, which loses data.** Render's
> free plan cannot attach a persistent disk, so `invite_finder.db` sits on an
> ephemeral filesystem and is wiped on every deploy, restart, and wake from
> idle spin-down (~15 min without traffic).
>
> For a service that takes payments this is a real failure, not a limitation:
> a customer pays, Stripe fires `checkout.session.completed`, the service cold
> starts with an empty database, `mark_order_paid()` finds no such order — and
> you have taken money and delivered nothing. The `http_cache` is wiped with
> it, so already-purchased enrichment gets bought from Apify/Perflo twice.
>
> Restoring durability means `plan: starter`, `numInstances: 1`, the `disk:`
> block, and `INVITE_DB_PATH=/data/invite_finder.db` — all four are written
> out in `render.yaml`'s header comment. The $50 hackathon credit covers the
> cost, but **Render still requires a card on file** to select a paid plan,
> which is what pushed this config to free in the first place.

`numInstances: 1` (in the paid config) is not a cost saving — a Render disk
can only attach to a single instance, which matches what this app needs
anyway (see the `--workers 1` note below).

### Tunnel to localhost (recommended while money is moving)

Strictly better than the free plan for a live demo: no card, no cold starts,
and SQLite persists on your own disk.

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000   # prints a public https URL
```

Point both webhooks at that URL (`/api/webhooks/linq`, `/api/webhooks/stripe`).
The only thing a deploy buys over this is surviving a closed laptop — which
the free plan does not reliably give you either, since it spins down anyway.

### Fly.io (previous)

Two apps, each with its own `Dockerfile` + `fly.toml`, both sized for Fly's
free monthly allowance (`shared-cpu-1x` / 256MB, scale-to-zero):

- Backend: `Dockerfile` + `fly.toml` at the repo root (`luma-connects-api`),
  with a 1GB volume mounted at `/data` for the SQLite file.
- Frontend: `invite_viewer/Dockerfile` + `invite_viewer/fly.toml`
  (`luma-connects`), stateless.

```bash
# one-time: Fly requires a payment method on file even for free-tier usage
# (visit https://fly.io/trial if you see "trial has ended")
flyctl auth login

# backend
flyctl apps create luma-connects-api
flyctl volumes create invite_finder_data --app luma-connects-api --region sjc --size 1
flyctl secrets set --app luma-connects-api \
  OPENAI_API_KEY=... BRIGHTDATA_API_KEY=... BRIGHTDATA_SERP_ZONE=... BRIGHTDATA_UNLOCKER_ZONE=...
flyctl deploy --app luma-connects-api

# frontend (build args in invite_viewer/fly.toml already point at
# https://luma-connects-api.fly.dev -- edit them first if you used different app names)
flyctl apps create luma-connects
cd invite_viewer && flyctl deploy --app luma-connects
```

`--workers 1` is load-bearing: the run orchestrator uses in-process asyncio
tasks and the SSE stream polls SQLite directly, both of which assume a
single process. Do not scale the backend beyond one machine without first
moving to Postgres + `LISTEN/NOTIFY` (the `run_events` table is designed to
make that migration straightforward later).

## Troubleshooting

- **`ConfigError: Missing required environment variable`** — you're not in
  offline mode and one of the three `BRIGHTDATA_*` vars is unset.
- **`CacheMiss` (503) in offline mode** — the request has never been made
  before; either seed the cache (see above) or drop `INVITE_OFFLINE`.
- **Chat/classification fails with "Missing credentials"** — no
  `OPENAI_API_KEY` in the backend's process environment. `Settings.from_env()`
  loads `.env` automatically (searching upward from the current working
  directory), so this usually means either the key is missing from `.env`,
  or you started uvicorn from outside the repo (no `.env` found on the
  search path). It's safe to set `OPENAI_API_KEY` directly in the shell too
  if you'd rather not rely on `.env` discovery. Luma ingestion and
  confirmed-guest linking still succeed independently either way; only SERP
  discovery, classification, and chat need OpenAI.
- **`sqlite3.ProgrammingError: SQLite objects created in a thread...`** —
  only relevant if you're editing `db.py`: connections are opened with
  `check_same_thread=False` deliberately, because FastAPI can resolve a sync
  dependency in a worker thread and use it from an async endpoint. Don't
  remove that flag.
- **A run is stuck "running" after a crash/restart** — the API's startup
  lifespan calls `run_store.reap_stuck_runs()`, which marks any run still
  `queued`/`running` at boot as `failed`. This is automatic; no action needed.
- **"Could not reach the Luma Connects API at http://localhost:XXXX"** — the
  frontend's `INVITE_API_BASE_URL`/`NEXT_PUBLIC_INVITE_API_BASE_URL` don't
  point at the port the backend is actually listening on. If you started the
  backend with a custom `--port`, update `invite_viewer/.env.local` to match
  (not just `.env.local.example`) and restart `npm run dev` — Next.js reads
  `NEXT_PUBLIC_*` vars at build/start time, so editing `.env.local` alone
  without restarting the dev server won't take effect.

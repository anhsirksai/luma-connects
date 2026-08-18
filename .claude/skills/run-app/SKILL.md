---
name: run-app
description: Launch Luma Connects locally (FastAPI backend + Next.js frontend) in offline demo mode or live mode, including how to seed cache-only demo data without spending Bright Data or OpenAI credits.
---

# Running Luma Connects locally

Two services, always run together. Full detail: `docs/RUNBOOK.md`.

## Offline / demo mode (zero Bright Data or OpenAI spend)

Use this to see the app working, or to test frontend/backend changes,
without touching any paid API.

```bash
# terminal 1 — backend. ADMIN_AUTH=off skips the passcode gate that .env's
# ADMIN_PHONE would otherwise turn on; PUBLIC_BASE_URL must come with it,
# because ADMIN_AUTH=off refuses to start on .env's tunnel URL.
INVITE_OFFLINE=1 ADMIN_AUTH=off PUBLIC_BASE_URL=http://localhost:8000 \
  BRIGHTDATA_API_KEY=x BRIGHTDATA_SERP_ZONE=x BRIGHTDATA_UNLOCKER_ZONE=x \
  .venv/bin/uvicorn invite_finder.api.app:app --port 8000 --reload

# terminal 2 — frontend
cd invite_viewer
npm install   # first time only
cp .env.local.example .env.local   # first time only, already points at :8000
npm run dev
```

Open `http://localhost:3000/events`.

With an empty cache, submitting a Luma URL fails fast with a `CacheMiss`
(503) — offline mode has nothing to serve and will not hit the network. To
see real data, seed the cache from the committed fixture first:

```python
# .venv/bin/python, from the repo root
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

Then submit `https://luma.com/vla-night-panel` from the "Add an event" form.
Luma ingestion (12 confirmed guests, real names + LinkedIn handles) succeeds
from cache. SERP discovery and classification still need a real
`OPENAI_API_KEY` to go further — without one, the run fails cleanly at the
`serp_discovery` phase and the confirmed guests remain visible on the event
page (nothing already fetched is lost).

To exercise the Room Snapshot and chat UI with a fuller, hand-seeded dataset
(12 confirmed + 4 inferred people, all pre-classified) instead of relying on
a live SERP/classification run, insert rows directly via
`invite_finder.store.{event_store,people_store,run_store}` — see the seeding
pattern in `tests/test_api.py` (`make_event`, `add_person`) for the exact
calls.

## Live mode (spends Bright Data + OpenAI credits)

```bash
# terminal 1 — backend; Settings.from_env() loads .env automatically.
# This form honours .env's ADMIN_PHONE, so the passcode gate is ON and the
# frontend will ask you to log in at /login. For a local run without it:
#   ./scripts/dev-open.sh
.venv/bin/uvicorn invite_finder.api.app:app --port 8000 --reload

# terminal 2 — frontend
cd invite_viewer && npm run dev
```

`.env` needs `OPENAI_API_KEY`, `BRIGHTDATA_API_KEY`, `BRIGHTDATA_SERP_ZONE`,
`BRIGHTDATA_UNLOCKER_ZONE`. Every Bright Data response is cached in SQLite
the moment it's fetched — re-analyzing the same event, or restarting either
server, never re-issues a request that's already been made.

## Verifying it's actually working

```bash
curl -s localhost:8000/api/health
# {"status":"ok","db":"ok","offline":false,"admin_auth":"off"}
# admin_auth tells you which mode you're in: "on" means every other curl
# below needs -H "Authorization: Bearer <token>" from /api/auth/verify.

curl -s -X POST localhost:8000/api/events -H 'content-type: application/json' \
  -d '{"luma_url":"https://luma.com/<slug>"}'
# 202 {"run_id":N,"event_id":null,"status":"queued"} for a new event
# 200 {"event_id":N,"status":"ready","already_cached":true} if already analyzed
```

For UI verification, prefer the `claude-in-chrome` skill to drive the actual
browser rather than just curling the API — the Room Snapshot percentages,
live SSE run progress, and chat card rendering all need visual confirmation,
not just a 200 status code.

## Stopping

```bash
lsof -ti:8000,3000 | xargs -r kill -9
```

# Luma Connects (frontend)

Next.js app for Luma Connects. Talks to the FastAPI backend in `src/invite_finder/api/` over HTTP -- see [`../docs/RUNBOOK.md`](../docs/RUNBOOK.md) for how to run both services together.

## Routes

- `/` -- marketing landing page
- `/events` -- event picker: date navigator, event cards, "add an event from Luma" form
- `/events/[id]` -- event detail: Room Snapshot breakdown, live run progress, and the "Super Connector" chat panel

## Run

```bash
npm install
cp .env.local.example .env.local   # point at your running backend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Check

```bash
npm run lint
npm run build
```

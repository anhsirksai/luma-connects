# Blindspots

A review pass over the repo at `b6d6c95`, looking for the mistakes that don't
announce themselves — things that work in every test and every demo, and fail
only when someone finds the URL, or when the second paying customer arrives.

**Nothing here is fixed.** Each entry is a finding plus the fix it wants, so
they can be triaged in order. The one thing that *was* implemented alongside
this report is the `ADMIN_AUTH` local toggle (see `docs/RUNBOOK.md`
§Operator auth), which is unrelated to the findings below.

Context that makes several of these live rather than theoretical: `.env`
points `PUBLIC_BASE_URL` at a public cloudflared tunnel, `/api/webhooks/*` is
ungated by design, and `STRIPE_WEBHOOK_SECRET` is deliberately unset.

Ranked. 1–6 involve money or access.

---

## 1. The Stripe webhook accepts forged payments right now

`stripe_links.verify_signature` (`src/invite_finder/stripe_links.py:41`)
returns `True` when the secret is empty, and `.env` leaves
`STRIPE_WEBHOOK_SECRET` unset on purpose. The route is ungated
(`api/app.py:103`) because Stripe cannot present a passcode, and the service
is reachable over a public tunnel.

So anyone who learns that URL can `POST /api/webhooks/stripe` with a
hand-written `checkout.session.completed` naming any pending order id, and
`deliver_paid_order` runs: real Apify/Perflo spend, and a stranger's dossier
texted out. Pending orders are created automatically on every Luma link and
every pasted guest list (`conversation.py:_open_pending_order`,
`_handle_guest_list`), so live targets always exist — even though
`STRIPE_PAYMENT_LINK` is unset and no real payment can occur yet.

The Linq webhook has the same shape (`linq.py:82`), currently covered because
`LINQ_WEBHOOK_SECRET` *is* set.

**Fix.** Invert the default: an empty secret means reject, not accept. Put the
permissive path behind an explicit `STRIPE_ALLOW_UNSIGNED=1` that
`Settings.from_env()` refuses when the base URL is not local — the same
pattern `ADMIN_AUTH=off` now uses, and for the same reason.

## 2. Stripe signatures never expire

`verify_signature` parses `t=` out of the header and then never looks at it
(`stripe_links.py:57-70`). Stripe's own tolerance is 300 seconds. Without the
age check, one captured webhook body stays replayable forever.
`settle_order`'s idempotency contains the blast radius to a single order, but
signature freshness is not something to delegate to a downstream guard.

**Fix.** Reject when `abs(now - t)` exceeds a configurable tolerance, default
300s.

## 3. The paid amount is never compared to the price

The Payment Link is priced "customer chooses price" — that is a hackathon
constraint, documented at `stripe_links.py:5-9`. `settle_order` takes an order
id and a session id and nothing else (`commerce_store.py:98`).
`checkout.amount_cents` is parsed and carried all the way to the webhook, then
used only for orphan logging.

A $100 FULL order therefore settles, grants its entitlement, and delivers on a
one-cent payment.

**Fix.** Compare `checkout.amount_cents` to `order["amount_cents"]` before
settling. Add `"underpaid"` as a third `settle_order` outcome: recorded, texted
back to the customer, never silently fulfilled. Some tolerance for currency
rounding is fine; an order of magnitude is not.

## 4. Paid orders can be lost with no retry path

Three gaps that compound:

**No exception handler.** `deliver_paid_order` (`conversation.py:311`) is
`try`/`finally` with no `except`, though its docstring promises it "must own
its own connection and never raise into the caller." A Linq timeout, a
provider error, or one bad row escapes into a bare task. The order stays
`status='paid'` and the customer gets nothing.

**No task reference.** `asyncio.create_task(deliver_paid_order(...))`
(`routes_webhooks.py:139`) and
`asyncio.create_task(_deliver_snapshot_when_ready(...))`
(`conversation.py:170`) both discard the handle. CPython only holds a weak
reference to a running task, so either can be garbage-collected mid-execution.
`RunManager` gets this right — it keeps `self._tasks` (`runner.py:60`) — which
is what makes the omission easy to miss in the other two.

**No recovery.** `reap_stuck_runs` runs in the startup lifespan for the `runs`
table (`api/app.py:48`). There is no equivalent for orders left in `paid`, so
a failure at any of the above is permanent and silent.

**Fix.** Wrap the body in `except Exception` that logs and texts an apology;
hold task handles in a module-level `set()` with a `discard` done-callback;
add `reap_undelivered_orders()` beside the run reaper.

## 5. The enrichment budget is a lifetime cap, not a per-order one

`ProviderRouter.remaining_cents()` calls `spend_cents(self.conn)` with no
`order_id` (`providers/router.py:112-117`). That sums `charged_cents` across
**all of `service_purchases`, for the life of the database**
(`commerce_store.py:359`). The ceiling defaults to 500 cents.

Once cumulative provider spend passes $5 — ever — every subsequent `fetch`
raises `BudgetExceeded`. `enrich_event_people` catches it and returns cleanly
by design (`enrich.py:189`), so the product keeps taking payments and keeps
delivering, just empty. Nothing errors. Nothing alerts.

The wiring for the right behaviour is already there and simply unused:
`ProviderRouter` stores `self.order_id`, and `spend_cents` already accepts
`order_id=`.

**Fix.** Pass `order_id=self.order_id` when it is set, and cap against that
order's own revenue rather than a flat constant. Keep the global ceiling as a
separate circuit breaker at a much higher number.

## 6. Six HTTP requests lock the operator out indefinitely

`verify_passcode` burns **every** live passcode as soon as one accumulates
more than `MAX_ATTEMPTS_PER_CODE` attempts (`auth.py:83-101`) — the reasoning
being that a code under attack is a code to throw away. But `/api/auth/verify`
is ungated and rate-limited only per code, so six bogus POSTs destroy the code
the operator just received and is halfway through typing.

`issue_passcode` allows a new one every 30s (`auth.py:29`), so the attack
sustains: verify six times, wait, repeat. The operator can never complete a
login, and every retry sends a real SMS to their real phone at real cost.

**Fix.** Rate-limit `/verify` per source address, not only per code. On
attempt exhaustion burn only the code being attacked. Prefer a cooldown after
N consecutive failures over invalidating outstanding codes.

## 7. "Reply START to resume" does not work

Both the STOP confirmation and the welcome text promise it
(`conversation.py:127` and `WELCOME`). But `is_suppressed` is checked in the
webhook *before* `handle_inbound` (`routes_webhooks.py:88`), which returns
`ignored="suppressed"` and stops. A suppressed handle's START is never
interpreted, and no unsuppress path exists anywhere in `src/`.

Suppression is permanent, and the product says otherwise — in the one class of
message where that claim is regulated rather than merely polite.

**Fix.** Check for START/UNSTOP before the suppression check and delete the
row. Or stop promising it. Not both.

## 8. Auth rows accumulate forever

`admin_passcodes` and `admin_sessions` are only ever deleted one row at a time,
on a Linq send failure (`auth_store.py:93`). Consumed codes and expired
sessions stay. `live_passcodes` filters correctly on every verify, so this
degrades slowly rather than breaking — but a session token hash that outlives
its own expiry by months is stored data with no purpose.

**Fix.** Prune both tables in the startup lifespan, next to the run reaper.

## 9. `/api/health` is ungated and reports the till

It returns `unfulfilled_payments_cents`, `unfulfilled_payments_count`, and
whether the passcode gate is on (`api/app.py:138-162`). Render needs the
endpoint unauthenticated for health checks; it does not need the money fields.
As it stands, anyone probing the tunnel learns whether the API is open before
bothering to try it.

**Fix.** Keep `status` and `db` public. Move the commerce counters behind
`require_admin`, or onto a separate `/api/admin/health`.

## 10. The container builds the app twice

`Dockerfile` ends with `python -m invite_finder.api.app`. That runs the module
as `__main__`, executing the module-level `app = create_app()`; then `main()`
calls `uvicorn.run("invite_finder.api.app:app")`, which imports the same module
again under its real name and builds a second app. The first is orphaned and
its lifespan never runs.

Harmless today. Confusing the first time a startup hook appears not to fire.

**Fix.**

```dockerfile
CMD ["uvicorn", "invite_finder.api.app:app", \
     "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
```

## 11. 156 tests, no CI

There is no `.github/`. Nothing runs `pytest` or `npm run build` on push, so
the guarantee in CLAUDE.md — "no test hits Bright Data, OpenAI, or the
network" — holds only as long as everyone remembers to check locally.
`tests/conftest.py` is load-bearing for that isolation (it stops a developer's
`.env` from silently gating the suite) and is equally unenforced.

**Fix.** One workflow: `pytest`, then
`cd invite_viewer && npm run lint && npm run build`.

## 12. Documentation that is now wrong

Cheap to fix, expensive to trip over. Corrected as part of this pass:

- `docs/RUNBOOK.md` claimed the Next.js frontend "does **not** yet send an
  admin token." It has since `b6d6c95`.
- `CLAUDE.md` said 143 tests; the count is 156.

Still outstanding:

- `invite_viewer/app/page.tsx` — the marketing homepage sells the previous
  product ("Score the invite fit", "compare runs, import new reports"). None
  of that is what the app does.
- `fly.toml` and `invite_viewer/fly.toml` — dead deploy target per CLAUDE.md.
- `pyproject.toml` — `name = "search-for-invite-bd"`, described as
  "discovering city-specific LinkedIn profiles", two renames ago.

## 13. Deliberate trade-offs, listed so they stay deliberate

Not bugs. Worth re-confirming rather than rediscovering:

- **The admin token is JS-readable.** It lives in a non-`httpOnly` cookie
  (`lib/api.ts:38-56`) and rides in the SSE query string (`runStreamUrl`).
  Both are forced by the architecture — client components fetch the backend
  directly, and `EventSource` cannot set headers. The cost is XSS-stealable
  sessions and tokens in access logs. The code says so in both places.
- **`render.yaml` is on the free plan with an ephemeral database.** Its own
  header comment explains, at length, that this loses payments. Still true.
- **`Settings.from_env()` raises at import time** when Bright Data keys are
  missing and offline is off, so a misconfigured start fails with a uvicorn
  traceback rather than a readable message.

#!/usr/bin/env bash
# Launch the backend locally with the admin passcode gate OFF.
#
# The plain `uvicorn` command keeps its normal meaning (gate on whenever
# ADMIN_PHONE is set in .env) — this script is the explicit local variant, so
# there is never any doubt about which mode is running.
#
# PUBLIC_BASE_URL is forced to localhost because .env usually points it at a
# cloudflared tunnel, and Settings.from_env() refuses ADMIN_AUTH=off on any
# non-local address. That refusal is the point: it is what stops this switch
# from ever reaching a public URL. Webhook callbacks will point at localhost
# for the duration, which is correct for a run that has no tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."
exec env ADMIN_AUTH=off PUBLIC_BASE_URL=http://localhost:8000 \
  .venv/bin/uvicorn invite_finder.api.app:app --port 8000 --reload "$@"

"""Collection-time setup shared by every test module.

pytest imports conftest.py before any test file, so this wins the race
against `load_dotenv_if_available()` picking up the developer's real `.env`.
That file is meant for the running server, not the test suite — its
ADMIN_PHONE in particular would otherwise silently gate every route in tests
that use the shared `app` singleton, since `load_dotenv()` only fills in
environment variables that aren't already set. Setting them here first (even
to empty string) is what keeps the suite isolated from whatever is configured
locally.
"""

from __future__ import annotations

import os

for _key in (
    "ADMIN_PHONE",
    "LINQ_API_KEY",
    "LINQ_WEBHOOK_SECRET",
    "STRIPE_PAYMENT_LINK",
    "STRIPE_WEBHOOK_SECRET",
    "PERFLO_AGENT_TOKEN",
    "PERFLO_MANDATE_ID",
    "APIFY_TOKEN",
):
    os.environ.setdefault(_key, "")

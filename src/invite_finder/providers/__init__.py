"""Channel-B data providers: pay-per-call enrichment behind one router.

Channel A is Bright Data (`invite_finder.brightdata`) — a subscription with
SERP and a page unlocker. Channel B is per-call and reaches the tools Bright
Data has no shape for: profile lookup by name, post feeds, X, contact data.

Nothing here is called directly by the pipeline; go through `ProviderRouter`,
which owns the result cache, the budget ceiling, and the cost ledger.
"""

from invite_finder.providers.base import (
    Capability,
    ProviderError,
    ProviderResult,
    BudgetExceeded,
)
from invite_finder.providers.router import ProviderRouter

__all__ = [
    "Capability",
    "ProviderError",
    "ProviderResult",
    "BudgetExceeded",
    "ProviderRouter",
]

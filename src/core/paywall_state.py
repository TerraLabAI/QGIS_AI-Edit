"""Free-tier paywall state: wall vs pre-wall vs normal.

Mirrors the website dashboard's classifyPaywallState (terralab-website
src/lib/dashboard/paywall-state.ts), so the plugin and the dashboard read a
free-tier balance the same way. The originating brief describes two
thresholds that overlap on paper ("wall: balance under 20 credits",
"pre-wall: balance between 1 and 20 credits inclusive"). Credits are only
ever spent in fixed per-generation blocks (a 1K generation costs
``unit_cost`` credits), so a real balance only ever lands on a multiple of
that block, and the website side resolved the overlap the same way this
module does: wall takes priority, pre-wall is what is left of its range
once wall is excluded (in practice exactly one generation's worth of
credits, never less).
"""
from __future__ import annotations

WALL = "wall"
PREWALL = "prewall"
NORMAL = "normal"


def classify_paywall_state(remaining: int, unit_cost: int) -> str:
    """"wall" | "prewall" | "normal" for a free-tier credit balance.

    ``unit_cost`` is the credit price of the cheapest generation (the 1K
    tier), passed in by the caller so this stays correct if that price ever
    changes server-side. Wall wins on overlap: a balance under one
    generation is a wall even though it also satisfies "prewall <= unit_cost".
    A non-positive ``unit_cost`` can never gate anything, so it reads as
    normal rather than dividing by zero or always walling.
    """
    if unit_cost <= 0:
        return NORMAL
    if remaining < unit_cost:
        return WALL
    if remaining <= unit_cost:
        return PREWALL
    return NORMAL


def total_free_generations(limit: int, unit_cost: int) -> int:
    """Total generations the monthly free credit quota buys, floored.

    E.g. 200 credits / 20 per 1K generation = 10; 60 / 20 = 3. Never a
    hardcoded generation count: always derived from the credit quota the
    server serves, so a quota change or a resolution repricing shows up
    without a plugin release.
    """
    if unit_cost <= 0 or limit <= 0:
        return 0
    return max(0, limit // unit_cost)

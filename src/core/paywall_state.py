













from __future__ import annotations

WALL = "wall"
PREWALL = "prewall"
NORMAL = "normal"


def classify_paywall_state(remaining: int, unit_cost: int) -> str:









    if unit_cost <= 0:
        return NORMAL
    if remaining < unit_cost:
        return WALL
    if remaining <= unit_cost:
        return PREWALL
    return NORMAL


def total_free_generations(limit: int, unit_cost: int) -> int:







    if unit_cost <= 0 or limit <= 0:
        return 0
    return max(0, limit // unit_cost)




SHIPPED_FREE_GENERATIONS = 3


def advertised_free_generations() -> int:









    from .config_store import get_export_dial

    served = get_export_dial(
        "entitlements.free_tier_generations", SHIPPED_FREE_GENERATIONS
    )
    return served if served > 0 else SHIPPED_FREE_GENERATIONS




















from __future__ import annotations

from .config_store import get_export_block, get_export_dial_str
from .resolution_labels import resolution_tiers


FREE_TIER_ALLOWED: tuple[str, ...] = ("1K",)
FREE_TIER_DEFAULT = "1K"

PAID_TIER_DEFAULT = "2K"


_MAX_FREE_TIERS = 8


def _served_free_plan() -> tuple[tuple[str, ...], str] | None:







    block = get_export_block("entitlements")
    if not block:
        return None
    raw_tiers = block.get("free_tier_tiers")
    raw_default = block.get("free_tier_default")
    if not isinstance(raw_tiers, (list, tuple)) or not raw_tiers:
        return None
    if not isinstance(raw_default, str):
        return None
    default = raw_default.strip()
    if not default:
        return None

    offered = resolution_tiers()
    served: list[str] = []
    for item in raw_tiers[:_MAX_FREE_TIERS]:
        if not isinstance(item, str):
            return None
        entry = item.strip()
        if entry not in offered:
            return None
        if entry not in served:
            served.append(entry)


    merged = list(FREE_TIER_ALLOWED)
    merged += [tier for tier in served if tier not in merged]
    if default not in merged:
        return None
    return tuple(merged), default


def free_tier_allowed_tiers() -> tuple[str, ...]:

    plan = _served_free_plan()
    return plan[0] if plan is not None else FREE_TIER_ALLOWED


def free_tier_default() -> str:


    plan = _served_free_plan()
    return plan[1] if plan is not None else FREE_TIER_DEFAULT


def paid_tier_default() -> str:




    return get_export_dial_str(
        "entitlements.paid_tier_default",
        PAID_TIER_DEFAULT,
        allowed=resolution_tiers(),
    )


def is_tier_allowed(tier: str, is_free_tier: bool) -> bool:

    if not is_free_tier:
        return True
    return tier in free_tier_allowed_tiers()


def coerce_tier(tier: str, is_free_tier: bool) -> str:



    if is_tier_allowed(tier, is_free_tier):
        return tier
    return free_tier_default()


def default_tier_for(is_free_tier: bool) -> str:

    return free_tier_default() if is_free_tier else paid_tier_default()

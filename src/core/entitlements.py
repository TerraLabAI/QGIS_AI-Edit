















from __future__ import annotations

from .config_store import ConfigMissing, get_export_block, require_str
from .resolution_labels import api_resolution_tiers, resolution_tiers


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



    offered = api_resolution_tiers()
    served: list[str] = []
    for item in raw_tiers[:_MAX_FREE_TIERS]:
        if not isinstance(item, str):
            return None
        entry = item.strip()


        if entry not in offered:
            continue
        if entry not in served:
            served.append(entry)
    if not served:
        return None

    if default not in served:
        return None
    return tuple(served), default


def _cheapest_tier() -> str:


    return resolution_tiers()[0]


def free_tier_allowed_tiers() -> tuple[str, ...]:

    plan = _served_free_plan()
    return plan[0] if plan is not None else (_cheapest_tier(),)


def free_tier_default() -> str:


    plan = _served_free_plan()
    return plan[1] if plan is not None else _cheapest_tier()


def paid_tier_default() -> str:




    try:
        tier = require_str("entitlements.paid_tier_default")
    except ConfigMissing:
        return _cheapest_tier()
    return tier if tier in resolution_tiers() else _cheapest_tier()


def is_tier_allowed(tier: str, is_free_tier: bool) -> bool:

    if not is_free_tier:
        return True
    return tier in free_tier_allowed_tiers()


def coerce_tier(tier: str, is_free_tier: bool) -> str:



    if tier not in api_resolution_tiers():

        return default_tier_for(is_free_tier)
    if is_tier_allowed(tier, is_free_tier):
        return tier
    return free_tier_default()


def coerce_dock_tier(tier: str, is_free_tier: bool) -> str:




    if tier not in resolution_tiers():
        return default_tier_for(is_free_tier)
    return coerce_tier(tier, is_free_tier)


def default_tier_for(is_free_tier: bool) -> str:

    return free_tier_default() if is_free_tier else paid_tier_default()

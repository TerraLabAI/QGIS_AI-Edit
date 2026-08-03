"""What each plan may pick among the output detail tiers.

One source of truth for three decisions that used to live as three separate
copies of the same literal: which tiers a free account may pick, which tier a
free account is coerced to, and which tier a paid account lands on before it
picks one itself. The picker, the click rejection and the submit-time coercion
all read from here, so they cannot drift apart.

Every value is server-readable through the export config, with the shipped
constant as the fallback: an empty store (startup, old server) or an invalid
value always returns what the plugin ships.

The free plan is the exception to the usual per-field read. What a free account
may render costs money on every generation, so widening it has to be deliberate
rather than a side effect of one key: the served block must name BOTH the tiers
the plan may pick and the tier it starts on, and both must be coherent, or the
shipped free plan applies whole. Union-only still holds on top of that, so a
tier the plugin ships as free can never be taken away.
"""
from __future__ import annotations

from .config_store import get_export_block, get_export_dial_str
from .resolution_labels import resolution_tiers

# Shipped plan rules.
FREE_TIER_ALLOWED: tuple[str, ...] = ("1K",)
FREE_TIER_DEFAULT = "1K"
# Paid accounts land on the second tier: better results out of the box.
PAID_TIER_DEFAULT = "2K"

# A plan offers a handful of tiers, never a catalogue.
_MAX_FREE_TIERS = 8


def _served_free_plan() -> tuple[tuple[str, ...], str] | None:
    """The served free plan as (allowed tiers, default tier), or None when the
    block is absent, incomplete or incoherent.

    Complete means both keys are present. Coherent means every named tier is
    one the picker actually offers, and the default is one of them. A single
    failed check drops the whole block, so a mistyped key leaves free accounts
    exactly where they are today instead of quietly widening them."""
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

    # Union-only: the shipped free tiers stay first and can never be dropped.
    merged = list(FREE_TIER_ALLOWED)
    merged += [tier for tier in served if tier not in merged]
    if default not in merged:
        return None
    return tuple(merged), default


def free_tier_allowed_tiers() -> tuple[str, ...]:
    """Tiers a free account may pick, shipped entries first."""
    plan = _served_free_plan()
    return plan[0] if plan is not None else FREE_TIER_ALLOWED


def free_tier_default() -> str:
    """Tier a free account is coerced to. Always one of the tiers the same
    block allows, so the two settings cannot contradict each other."""
    plan = _served_free_plan()
    return plan[1] if plan is not None else FREE_TIER_DEFAULT


def paid_tier_default() -> str:
    """Tier a paid account starts on before it picks one itself. Restricted to
    the tiers the picker actually offers. Read per field rather than as a
    block: a paid account may already pick every tier, so moving its starting
    point widens nothing."""
    return get_export_dial_str(
        "entitlements.paid_tier_default",
        PAID_TIER_DEFAULT,
        allowed=resolution_tiers(),
    )


def is_tier_allowed(tier: str, is_free_tier: bool) -> bool:
    """Whether this plan may pick this tier. Paid accounts may pick any."""
    if not is_free_tier:
        return True
    return tier in free_tier_allowed_tiers()


def coerce_tier(tier: str, is_free_tier: bool) -> str:
    """The tier to actually use: the pick when the plan allows it, the plan's
    default otherwise. Used at submit time so a stale selection can never
    quote a price the account cannot pay."""
    if is_tier_allowed(tier, is_free_tier):
        return tier
    return free_tier_default()


def default_tier_for(is_free_tier: bool) -> str:
    """Where a freshly confirmed account starts."""
    return free_tier_default() if is_free_tier else paid_tier_default()

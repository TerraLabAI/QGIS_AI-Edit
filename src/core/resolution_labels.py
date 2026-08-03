"""User-facing labels for the output resolution tiers.

The internal keys ("1K"/"2K"/"4K") are the canonical identifiers sent to the
server and used for credit-cost and pixel-target lookups; they MUST stay
unchanged (server backward compatibility). Only the text shown to the user is
remapped here to quality wording. Callers keep the raw key for logic and pass
it through this function purely at the display boundary.
"""

from __future__ import annotations

from .config_store import get_export_copy, get_export_dial_seq
from .i18n import tr

# Fallback until the server config loads; server values override via set_resolution_credit_costs.
DEFAULT_RESOLUTION_CREDIT_COSTS: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}

# The tiers the picker offers, in display order (least detail first). Their
# price and their pixel budget are already per-entry dials; this is the set
# itself, so a new tier can ship from the server alone.
DEFAULT_RESOLUTION_TIERS: tuple[str, ...] = ("1K", "2K", "4K")

# Cap on the menu length, so one bad deploy cannot make the picker unusable.
_MAX_RESOLUTION_TIERS = 8


def resolution_tiers() -> tuple[str, ...]:
    """Ordered tiers to offer, shipped tuple first then server additions.

    Union-only: a deploy can add a tier but never drop a shipped one, so the
    picker can never lose the tier the user is currently on. A served list
    that is empty, malformed, or names none of the shipped tiers is treated
    as a mis-keyed config and ignored, leaving the shipped tuple. An added
    tier with no label of its own still renders: the label helpers pass an
    unknown key through unchanged.
    """
    return get_export_dial_seq(
        "resolution_tiers",
        DEFAULT_RESOLUTION_TIERS,
        max_len=_MAX_RESOLUTION_TIERS,
        require_base_overlap=True,
    )


def shipped_tier_name(resolution: str) -> str | None:
    """The quality word this plugin ships for a tier, None for an unknown one.

    The tr() calls stay literal here so the i18n extractor keeps finding the
    three source strings.
    """
    return {
        "1K": tr("Standard"),
        "2K": tr("Detailed"),
        "4K": tr("Maximum"),
    }.get(resolution)


def resolution_quality_name(resolution: str | None) -> str | None:
    """Map an internal resolution key to its user-facing quality tier name.

    Returns just the quality word ("Standard"/"Detailed"/"Maximum"), or the value
    unchanged (including None) for any unknown key so future tiers still
    render. Callers that want the resolution shown alongside use
    ``resolution_display_label`` instead.

    The word itself is served copy (``resolution.label.<tier>``), which is what
    makes a tier added through ``resolution_tiers`` above complete: the picker
    can offer it, price it and size it from the server, and now name it too,
    instead of showing the bare key until the next release.
    """
    if resolution is None:
        return None
    # An unknown tier has no shipped word, so the raw key is what it falls back
    # to, exactly as before a label could be served for it.
    fallback = shipped_tier_name(resolution) or resolution
    return get_export_copy(f"resolution.label.{resolution}", fallback, max_chars=40)


def resolution_display_label(resolution: str | None) -> str | None:
    """Quality tier name with the underlying resolution in parentheses
    ("Standard (1K)"), for the version-details popup.

    Unknown values (including None) pass through unchanged.
    """
    name = resolution_quality_name(resolution)
    # Unknown keys map to themselves; don't render them doubled ("8K (8K)").
    if name is None or name == resolution:
        return name
    return f"{name} ({resolution})"


def resolution_chip_label(resolution: str | None) -> str | None:
    """Quality tier name for the footer chip when the picker is closed
    ("Standard").

    The open menu rows append the exact resolution in parentheses
    ("Standard (1K)"). Unknown values (including None) pass through unchanged.
    """
    return resolution_quality_name(resolution)

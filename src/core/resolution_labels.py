"""User-facing labels for the output resolution tiers.

The internal keys ("1K"/"2K"/"4K") are the canonical identifiers sent to the
server and used for credit-cost and pixel-target lookups; they MUST stay
unchanged (server backward compatibility). Only the text shown to the user is
remapped here to quality wording. Callers keep the raw key for logic and pass
it through this function purely at the display boundary.
"""

from __future__ import annotations

from .i18n import tr


def resolution_quality_name(resolution: str | None) -> str | None:
    """Map an internal resolution key to its user-facing quality tier name.

    Returns just the quality word ("Standard"/"Detailed"/"Maximum"), or the value
    unchanged (including None) for any unknown key so future tiers still
    render. Callers that want the resolution shown alongside use
    ``resolution_display_label`` instead.
    """
    return {
        "1K": tr("Standard"),
        "2K": tr("Detailed"),
        "4K": tr("Maximum"),
    }.get(resolution, resolution)


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

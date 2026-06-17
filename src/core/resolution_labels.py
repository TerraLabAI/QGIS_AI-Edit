








from __future__ import annotations

from .config_store import get_export_copy, get_export_dial_seq
from .i18n import tr


DEFAULT_RESOLUTION_CREDIT_COSTS: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}




DEFAULT_RESOLUTION_TIERS: tuple[str, ...] = ("1K", "2K", "4K")


_MAX_RESOLUTION_TIERS = 8


def resolution_tiers() -> tuple[str, ...]:









    return get_export_dial_seq(
        "resolution_tiers",
        DEFAULT_RESOLUTION_TIERS,
        max_len=_MAX_RESOLUTION_TIERS,
        require_base_overlap=True,
    )


def shipped_tier_name(resolution: str) -> str | None:










    return {
        "1K": tr("Standard"),
        "2K": tr("Detailed"),
        "4K": tr("Maximum"),
    }.get(resolution)


def resolution_quality_name(resolution: str | None) -> str | None:












    if resolution is None:
        return None


    fallback = shipped_tier_name(resolution) or resolution
    return get_export_copy(
        f"resolution.quality_label.{resolution}", fallback, max_chars=40
    )


def resolution_display_label(resolution: str | None) -> str | None:





    name = resolution_quality_name(resolution)

    if name is None or name == resolution:
        return name
    return f"{name} ({resolution})"


def resolution_chip_label(resolution: str | None) -> str | None:






    return resolution_quality_name(resolution)










from __future__ import annotations

from collections.abc import Mapping

from .config_store import ConfigMissing, get_export_copy, get_export_dial_seq, require_table
from .i18n import tr


def served_credit_costs() -> dict[str, int]:






    try:
        table = require_table("resolution_credit_costs")
    except ConfigMissing:
        return {}
    if not isinstance(table, dict):
        return {}
    costs: dict[str, int] = {}
    for tier, value in list(table.items())[:16]:
        if isinstance(tier, str) and not isinstance(value, bool) and isinstance(value, (int, float)):
            if value > 0 and float(value).is_integer():
                costs[tier] = int(value)
    return costs


class _ServedCreditCosts(Mapping):




    def __getitem__(self, tier):
        return served_credit_costs()[tier]

    def __iter__(self):
        return iter(served_credit_costs())

    def __len__(self):
        return len(served_credit_costs())



DEFAULT_RESOLUTION_CREDIT_COSTS = _ServedCreditCosts()




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

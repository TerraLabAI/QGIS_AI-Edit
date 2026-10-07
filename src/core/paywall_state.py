








from __future__ import annotations

WALL = "wall"
PREWALL = "prewall"
NORMAL = "normal"
PRO_LOW = "pro_low"
_STATES = frozenset({WALL, PREWALL, NORMAL, PRO_LOW})



_last: dict = {"state": None, "epoch": 0}


def usage_epoch() -> int:



    return int(_last["epoch"])


def remember_usage(usage, epoch: int | None = None) -> None:



    if epoch is not None and epoch != _last["epoch"]:
        return
    if not isinstance(usage, dict) or "images_used" not in usage:
        return
    state = usage.get("paywall_state")
    _last["state"] = state if isinstance(state, str) and state in _STATES else None


def forget() -> None:

    _last["state"] = None
    _last["epoch"] = int(_last["epoch"]) + 1


def remember_refusal_wall() -> None:



    _last["state"] = WALL


def served_paywall_state() -> str | None:

    return _last["state"]


def total_free_generations(limit: int, unit_cost: int) -> int:







    if unit_cost <= 0 or limit <= 0:
        return 0
    return max(0, limit // unit_cost)


def advertised_free_generations() -> int | None:



    from .config_store import ConfigMissing, require_dial

    try:
        served = require_dial("entitlements.free_tier_generations", lo=1)
    except ConfigMissing:
        return None
    return int(served)

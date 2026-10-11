








from __future__ import annotations

WALL = "wall"
PREWALL = "prewall"
NORMAL = "normal"
PRO_LOW = "pro_low"
_STATES = frozenset({WALL, PREWALL, NORMAL, PRO_LOW})





_last: dict = {"state": None, "epoch": 0, "account_cap": None}


def usage_epoch() -> int:



    return int(_last["epoch"])


def remember_usage(usage, epoch: int | None = None) -> None:



    if epoch is not None and epoch != _last["epoch"]:
        return
    if not isinstance(usage, dict) or "images_used" not in usage:
        return
    state = usage.get("paywall_state")
    _last["state"] = state if isinstance(state, str) and state in _STATES else None
    cap = usage.get("free_account_cap")
    if isinstance(cap, dict) and cap.get("blocked") is True:
        if _last["account_cap"] is None:
            _last["account_cap"] = ""
    else:
        _last["account_cap"] = None


def forget() -> None:

    _last["state"] = None
    _last["account_cap"] = None
    _last["epoch"] = int(_last["epoch"]) + 1


def remember_refusal_wall() -> None:



    _last["state"] = WALL


def remember_account_cap_refusal(result) -> None:


    if not isinstance(result, dict) or result.get("reason") != "free_account_cap":
        return
    if str(result.get("code") or "").strip().upper() != "TRIAL_EXHAUSTED":
        return
    error = result.get("error")
    _last["account_cap"] = error.strip() if isinstance(error, str) else ""


def split_cap_text(text: str) -> tuple[str, str]:


    import re

    text = (text or "").strip()
    match = re.search(r"[.!?](\s+)|[。！？]", text)
    if not match:
        return text, ""
    return text[:match.end()].strip(), text[match.end():].strip()


def account_cap_blocked() -> str | None:


    return _last["account_cap"]


def served_paywall_state() -> str | None:

    return _last["state"]


def advertised_free_generations() -> int | None:



    from .config_store import ConfigMissing, require_dial

    try:
        served = require_dial("entitlements.free_tier_generations", lo=1)
    except ConfigMissing:
        return None
    return int(served)

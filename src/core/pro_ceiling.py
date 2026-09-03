"""Served dials for the paid-tier "contact us for a custom plan" path.

A Pro account that runs low on, or out of, its monthly credits is offered a
human contact instead of a self-serve wall. Every value here comes from the
activation config (``pro_ceiling`` block), with one shipped fallback each, so
the address, the threshold and the wording follow the website without a
plugin release. Reads are cache-only: never a network call.
"""
from __future__ import annotations

from .auth.activation_manager import _is_safe_email, get_server_config, is_feature_enabled

DEFAULT_CONTACT_EMAIL = "yvann.barbot@terra-lab.ai"
DEFAULT_LOW_FRACTION = 0.10
_MAX_EMAIL_CHARS = 120
_MAX_LOW_FRACTION = 0.5


def _block() -> dict:
    block = get_server_config().get("pro_ceiling")
    return block if isinstance(block, dict) else {}


def pro_ceiling_enabled() -> bool:
    """Fail-open: only an explicit false in either switch turns it off."""
    if not is_feature_enabled("pro_ceiling"):
        return False
    return _block().get("enabled") is not False


def pro_ceiling_contact_email() -> str:
    value = _block().get("contact_email")
    if isinstance(value, str) and len(value) <= _MAX_EMAIL_CHARS and _is_safe_email(value):
        return value.strip()
    return DEFAULT_CONTACT_EMAIL


def pro_ceiling_low_fraction() -> float:
    """Share of the monthly limit under which the "running low" banner
    shows. Clamped to [0, 0.5]: half the month's credits is not low."""
    value = _block().get("low_fraction")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_LOW_FRACTION
    if value != value:  # NaN
        return DEFAULT_LOW_FRACTION
    return min(max(float(value), 0.0), _MAX_LOW_FRACTION)


def pro_low_threshold(limit: int) -> int:
    """How many remaining credits count as "running low" for ``limit``."""
    if not isinstance(limit, int) or limit <= 0:
        return 0
    return int(limit * pro_ceiling_low_fraction())

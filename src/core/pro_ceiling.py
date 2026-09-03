







from __future__ import annotations

from .auth.activation_manager import (
    _is_safe_email,
    get_contact_page_url,
    get_server_config,
    is_feature_enabled,
)

_MAX_EMAIL_CHARS = 120


def _block() -> dict:
    block = get_server_config().get("pro_ceiling")
    return block if isinstance(block, dict) else {}


def pro_ceiling_enabled() -> bool:

    if not is_feature_enabled("pro_ceiling"):
        return False
    return _block().get("enabled") is not False


def pro_ceiling_contact_email() -> str:
    value = _block().get("contact_email")
    if isinstance(value, str) and len(value) <= _MAX_EMAIL_CHARS and _is_safe_email(value):
        return value.strip()


    return get_contact_page_url()


def is_contact_link(value: str) -> bool:

    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))

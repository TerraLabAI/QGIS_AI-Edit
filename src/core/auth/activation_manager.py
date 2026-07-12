"""Activation manager for the AI Edit plugin.

Validates activation keys against the TerraLab backend.
"""
from __future__ import annotations

import re

from qgis.core import QgsSettings

from ..i18n import tr
from .auth_helper import (
    clear_activation as _auth_clear_activation,
)
from .auth_helper import (
    get_activation_key as _auth_get_activation_key,
)
from .auth_helper import (
    migrate_legacy_key as _auth_migrate_legacy_key,
)
from .auth_helper import (
    save_activation as _auth_save_activation,
)

_KEY_RE = re.compile(r"^tl_[0-9a-f]{32}$")

SETTINGS_PREFIX = "AIEdit/"


def build_utm_url(path: str, utm_content: str) -> str:
    """Campaign-tagged terra-lab.ai URL; CTAs differ only by path + utm_content."""
    return (
        f"https://terra-lab.ai{path}"
        f"?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content={utm_content}"
    )


SUBSCRIBE_URL = build_utm_url("/dashboard/ai-edit", "subscribe")
DASHBOARD_URL = build_utm_url("/dashboard/ai-edit", "dashboard")
TERMS_URL = build_utm_url("/terms-of-sale", "settings_terms")
PRIVACY_URL = build_utm_url("/privacy-policy", "settings_privacy")


def get_terms_url() -> str:
    return TERMS_URL


def get_privacy_url() -> str:
    return PRIVACY_URL


# Hardcoded fallback config (used when server is unreachable).
# free_credits mirrors the server's monthly free allowance in CREDITS
# (100 credits = 5 generations per month, renewed on the 1st).
DEFAULT_CONFIG = {
    "free_credits": 100,
    "free_tier_active": True,
    "upgrade_url": build_utm_url("/dashboard/ai-edit", "upgrade"),
}


def get_activation_key(settings=None) -> str:
    return _auth_get_activation_key(settings)


def has_consent(settings=None) -> bool:
    """Check if the user has accepted terms and privacy policy."""
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}consent_accepted", False, type=bool)


def save_consent(settings=None):
    """Mark that the user accepted terms and privacy policy."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}consent_accepted", True)


def save_activation(key: str, settings=None):
    """Save activation key (encrypted via QgsAuthManager when available)."""
    _auth_save_activation(key, settings)


def clear_activation(settings=None):
    """Clear activation state and the activation timestamp cohort marker."""
    _auth_clear_activation(settings)


def migrate_legacy_key(settings=None) -> bool:
    """Migrate any QSettings-only key to QgsAuthManager. Idempotent."""
    return _auth_migrate_legacy_key(settings)


def validate_key_with_server(client, key: str) -> tuple[bool, str, str]:
    """Validate an activation key against the server.

    Returns (success, message, error_code, usage). ``usage`` is the raw
    /usage payload on success (the validation call IS a usage fetch, so
    callers can reuse it instead of fetching credits a second time); None
    on failure.
    """
    key = key.strip()
    if not key:
        return False, tr("Please enter your activation key."), "NO_KEY", None

    if not _KEY_RE.match(key):
        return False, tr(
            "That does not look like an activation key. Most people do not need "
            "one: just use the Sign in button. A key starts with tl_ and is only "
            "for admin-issued or offline activation."
        ), "INVALID_FORMAT", None

    # Call /api/plugin/usage with the key as Bearer token
    auth = {
        "Authorization": f"Bearer {key}",
        "X-Product-ID": "ai-edit",
    }
    try:
        result = client.get_usage(auth=auth)
    except Exception:
        return (
            False,
            tr("Cannot reach server. Check your internet connection."),
            "NO_CONNECTION",
            None,
        )

    if "error" in result:
        code = (result.get("code", "") or "").strip().upper()
        error_msg = result.get("error", tr("Validation failed."))

        if code == "TRIAL_EXHAUSTED":
            return False, error_msg, "TRIAL_EXHAUSTED", None

        if code in {
            "QUOTA_EXCEEDED",
            "LIMIT_REACHED",
            "USAGE_LIMIT_REACHED",
            "MONTHLY_LIMIT_REACHED",
        }:
            return False, error_msg, "QUOTA_EXCEEDED", None

        if code == "INVALID_KEY":
            return (
                False,
                tr("Invalid activation key. Check your key and try again."),
                code,
                None,
            )
        if code == "SUBSCRIPTION_INACTIVE":
            return (
                False,
                tr(
                    "Your subscription has expired or been canceled. Renew at terra-lab.ai/dashboard"  # noqa: E501
                ),
                code,
                None,
            )
        return False, error_msg, (code or "VALIDATION_FAILED"), None

    server_product = result.get("product_id", "")
    if server_product and server_product != "ai-edit":
        return (
            False,
            tr("This key belongs to a different product. Use your AI Edit key."),
            "WRONG_PRODUCT",
            None,
        )

    return True, tr("Activation key verified!"), "", result


def get_subscribe_url() -> str:
    return SUBSCRIBE_URL


def get_dashboard_url() -> str:
    return DASHBOARD_URL


def get_tutorial_url(client=None) -> str:
    """Get tutorial URL from server config, falling back to product page."""
    config = get_server_config(client)
    return config.get("tutorial_url", "https://terra-lab.ai/blog/ai-edit-complete-guide")


# -- Server config (delegates to ConfigStore so unload + Plugin Reloader stay clean) --


def get_server_config(client=None) -> dict:
    """Fetch server-driven config, with local caching and fallback."""
    from ..config_store import get_store
    store = get_store()
    if store is not None:
        cached = store.get_activation_config()
        if cached is not None:
            return cached

    if client is None:
        return DEFAULT_CONFIG

    try:
        result = client.get_config("ai-edit")
        if "error" not in result:
            if store is not None:
                store.set_activation_config(result)
            return result
    except Exception:
        pass  # nosec B110

    return DEFAULT_CONFIG


def clear_config_cache():
    """Clear cached config (e.g. on plugin reload)."""
    from ..config_store import get_store
    store = get_store()
    if store is not None:
        store.clear_activation_config()

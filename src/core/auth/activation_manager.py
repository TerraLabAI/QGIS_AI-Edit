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
SUBSCRIBE_URL = (
    "https://terra-lab.ai/dashboard/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=subscribe"
)
DASHBOARD_URL = (
    "https://terra-lab.ai/dashboard/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dashboard"
)
TERMS_URL = (
    "https://terra-lab.ai/terms-of-sale"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=settings_terms"
)
PRIVACY_URL = (
    "https://terra-lab.ai/privacy-policy"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=settings_privacy"
)


def get_terms_url() -> str:
    return TERMS_URL


def get_privacy_url() -> str:
    return PRIVACY_URL


# Hardcoded fallback config (used when server is unreachable)
DEFAULT_CONFIG = {
    "free_credits": 5,
    "free_tier_active": True,
    "upgrade_url": (
        "https://terra-lab.ai/dashboard/ai-edit"
        "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=upgrade"
    ),
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

    Returns (success, message, error_code).
    """
    key = key.strip()
    if not key:
        return False, tr("Please enter your activation key."), "NO_KEY"

    if not _KEY_RE.match(key):
        return False, tr("Invalid key format. Keys look like tl_ followed by 32 characters."), "INVALID_FORMAT"

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
        )

    if "error" in result:
        code = (result.get("code", "") or "").strip().upper()
        error_msg = result.get("error", tr("Validation failed."))
        error_lower = error_msg.lower()

        if code == "TRIAL_EXHAUSTED" or "free credits used" in error_lower:
            return False, error_msg, "TRIAL_EXHAUSTED"

        if code in {
            "QUOTA_EXCEEDED",
            "LIMIT_REACHED",
            "USAGE_LIMIT_REACHED",
            "MONTHLY_LIMIT_REACHED",
        } or "monthly limit reached" in error_lower:
            return False, error_msg, "QUOTA_EXCEEDED"

        if code == "INVALID_KEY":
            return (
                False,
                tr("Invalid activation key. Check your key and try again."),
                code,
            )
        if code == "SUBSCRIPTION_INACTIVE":
            return (
                False,
                tr(
                    "Your subscription has expired or been canceled. Renew at terra-lab.ai/dashboard"  # noqa: E501
                ),
                code,
            )
        return False, error_msg, (code or "VALIDATION_FAILED")

    server_product = result.get("product_id", "")
    if server_product and server_product != "ai-edit":
        return (
            False,
            tr("This key belongs to a different product. Use your AI Edit key."),
            "WRONG_PRODUCT",
        )

    return True, tr("Activation key verified!"), ""


def get_subscribe_url() -> str:
    return SUBSCRIBE_URL


def get_dashboard_url() -> str:
    return DASHBOARD_URL


def get_tutorial_url(client=None) -> str:
    """Get tutorial URL from server config, falling back to product page."""
    config = get_server_config(client)
    return config.get("tutorial_url", "https://youtu.be/8qiNQVCGlsQ?si=Ps0XjBT8LW_1svkg")


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

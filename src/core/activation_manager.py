"""Activation manager for the AI Edit plugin.

Validates activation keys against the TerraLab backend.
"""
from __future__ import annotations

from qgis.core import QgsSettings

from .i18n import tr

SETTINGS_PREFIX = "AIEdit/"
SUBSCRIBE_URL = (
    "https://terra-lab.ai/dashboard/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=subscribe"
)
DASHBOARD_URL = (
    "https://terra-lab.ai/dashboard/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dashboard"
)

# Hardcoded fallback config (used when server is unreachable)
DEFAULT_CONFIG = {
    "free_credits": 5,
    "free_tier_active": True,
    "promo_active": False,
    "upgrade_url": (
        "https://terra-lab.ai/dashboard/ai-edit"
        "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=upgrade"
    ),
}


def get_activation_key(settings=None) -> str:
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}activation_key", "")


def has_consent(settings=None) -> bool:
    """Check if the user has accepted terms and privacy policy."""
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}consent_accepted", False, type=bool)


def save_consent(settings=None):
    """Mark that the user accepted terms and privacy policy."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}consent_accepted", True)


def save_activation(key: str, settings=None):
    """Save activation key."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}activation_key", key.strip())


def clear_activation(settings=None):
    """Clear activation state (e.g. when key becomes invalid)."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}activation_key", "")


def validate_key_with_server(client, key: str) -> tuple[bool, str, str]:
    """Validate an activation key against the server.

    Returns (success, message, error_code).
    """
    key = key.strip()
    if not key:
        return False, tr("Please enter your activation key."), "NO_KEY"

    if not key.startswith("tl_"):
        return False, tr("Invalid key format. Keys start with tl_"), "INVALID_FORMAT"

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
    return config.get("tutorial_url", "https://youtu.be/d8D_GmaX9NM?si=UpLtVl3biKIB2HuY")


# -- Server config --

_cached_config: dict | None = None


def get_server_config(client=None) -> dict:
    """Fetch server-driven config, with local caching and fallback."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    if client is None:
        return DEFAULT_CONFIG

    try:
        result = client.get_config("ai-edit")
        if "error" not in result:
            _cached_config = result
            return result
    except Exception:
        pass  # nosec B110

    return DEFAULT_CONFIG


def clear_config_cache():
    """Clear cached config (e.g. on plugin reload)."""
    global _cached_config
    _cached_config = None

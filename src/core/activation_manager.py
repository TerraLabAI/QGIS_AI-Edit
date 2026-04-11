"""Activation manager for the AI Edit plugin.

Validates activation keys against the TerraLab backend.
"""
from __future__ import annotations

import uuid

from qgis.core import QgsSettings

from .i18n import tr

SETTINGS_PREFIX = "AIEdit/"
TERRALAB_PREFIX = "TerraLab/"
SUBSCRIBE_URL = "https://terra-lab.ai/dashboard/ai-edit?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=subscribe"
DASHBOARD_URL = "https://terra-lab.ai/dashboard/ai-edit?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dashboard"

# Hardcoded fallback config (used when server is unreachable)
DEFAULT_CONFIG = {
    "free_credits": 5,
    "free_tier_active": True,
    "promo_active": True,
    "promo_code": "EARLYBIRD",
    "promo_text": "Launch offer: first month at 13 EUR instead of 19 EUR. Code: EARLYBIRD",
    "upgrade_url": "https://terra-lab.ai/dashboard/ai-edit?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=upgrade",
}


def is_activated(settings=None) -> bool:
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}activated", False, type=bool)


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
    """Save activation key and mark as activated."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}activation_key", key.strip())
    s.setValue(f"{SETTINGS_PREFIX}activated", True)


def clear_activation(settings=None):
    """Clear activation state (e.g. when key becomes invalid)."""
    s = settings or QgsSettings()
    s.setValue(f"{SETTINGS_PREFIX}activation_key", "")
    s.setValue(f"{SETTINGS_PREFIX}activated", False)


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

    return True, tr("Activation key verified!"), ""


def get_subscribe_url() -> str:
    return SUBSCRIBE_URL


def get_dashboard_url() -> str:
    return DASHBOARD_URL


def get_tutorial_url(client=None) -> str:
    """Get tutorial URL from server config, falling back to product page."""
    config = get_server_config(client)
    return config.get("tutorial_url", "https://youtu.be/d8D_GmaX9NM?si=UpLtVl3biKIB2HuY")


# -- Device ID management --

def get_device_id(settings=None) -> str:
    """Get or generate a persistent device ID."""
    s = settings or QgsSettings()
    device_id = s.value(f"{TERRALAB_PREFIX}device_id", "")
    if not device_id:
        device_id = str(uuid.uuid4())
        s.setValue(f"{TERRALAB_PREFIX}device_id", device_id)
    return device_id


# -- Cross-plugin email sharing --

def get_shared_email(settings=None) -> str:
    """Get email from shared TerraLab namespace (set by any plugin)."""
    s = settings or QgsSettings()
    return s.value(f"{TERRALAB_PREFIX}user_email", "")


def save_shared_email(email: str, settings=None):
    """Save email to shared TerraLab namespace for cross-plugin use."""
    s = settings or QgsSettings()
    s.setValue(f"{TERRALAB_PREFIX}user_email", email.strip())


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
        pass

    return DEFAULT_CONFIG


def clear_config_cache():
    """Clear cached config (e.g. on plugin reload)."""
    global _cached_config
    _cached_config = None


# -- Magic link signup --

def send_magic_link(client, email: str) -> tuple[bool, str]:
    """Send a magic link for free tier signup.

    Returns (success, message) where message is the English source string
    (caller should pass through tr() for display).
    """
    email = email.strip()
    if not email or "@" not in email:
        return False, "Please enter a valid email address."

    device_id = get_device_id()
    try:
        result = client.send_magic_link(email, device_id, "ai-edit")
    except Exception:
        return False, "Could not send the email. Please try again."

    if result.get("ok"):
        save_shared_email(email)
        return True, "Check your email! Click the link to access your dashboard."

    reason = result.get("reason", "")
    reason_map = {
        "INVALID_EMAIL": "Please enter a valid email address.",
        "DEVICE_ALREADY_USED": "Free edits already claimed on this device.",
        "RATE_LIMITED": "Too many attempts. Please wait a moment.",
        "ALREADY_REGISTERED": "This email already has free edits. Check your dashboard.",
        "EMAIL_SEND_FAILED": "Could not send the email. Please try again.",
    }
    return False, reason_map.get(reason, "Could not send the email. Please try again.")

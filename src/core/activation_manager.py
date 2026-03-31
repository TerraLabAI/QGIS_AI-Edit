"""Activation manager for the AI Edit plugin.

Validates activation keys against the TerraLab backend.
"""

from typing import Tuple
from qgis.core import QgsSettings

SETTINGS_PREFIX = "AIEdit/"
SUBSCRIBE_URL = "https://terra-lab.ai/ai-edit"


def is_activated(settings=None) -> bool:
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}activated", False, type=bool)


def get_activation_key(settings=None) -> str:
    s = settings or QgsSettings()
    return s.value(f"{SETTINGS_PREFIX}activation_key", "")


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


def validate_key_with_server(client, key: str) -> Tuple[bool, str]:
    """Validate an activation key against the server.

    Returns (success, message).
    """
    key = key.strip()
    if not key:
        return False, "Please enter your activation key."

    if not key.startswith("tl_pro_"):
        return False, "Invalid key format. Keys start with tl_pro_"

    # Call /api/plugin/usage with the key as Bearer token
    auth = {
        "Authorization": f"Bearer {key}",
        "X-Product-ID": "ai-edit",
    }
    try:
        result = client.get_usage(auth=auth)
    except Exception:
        return False, "Cannot reach server. Check your internet connection."

    if "error" in result:
        code = result.get("code", "")
        if code == "INVALID_KEY":
            return False, "Invalid activation key. Check your key and try again."
        if code == "SUBSCRIPTION_INACTIVE":
            return False, "Subscription expired. Renew at terra-lab.ai/dashboard"
        return False, result.get("error", "Validation failed.")

    return True, "Activation key verified!"


def get_subscribe_url() -> str:
    return SUBSCRIBE_URL

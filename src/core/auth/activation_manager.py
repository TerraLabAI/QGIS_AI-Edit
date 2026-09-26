



from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..config_store import get_export_copy
from ..i18n import tr

_KEY_RE = re.compile(r"^tl_[0-9a-f]{32}$")

SETTINGS_PREFIX = "AIEdit/"


def build_utm_url(path: str, utm_content: str) -> str:

    return (
        f"https://terra-lab.ai{path}"
        f"?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content={utm_content}"
    )


SUBSCRIBE_URL = build_utm_url("/dashboard/ai-edit", "subscribe")
DASHBOARD_URL = build_utm_url("/dashboard/ai-edit", "dashboard")



WALL_URL = build_utm_url("/dashboard/ai-edit", "wall")
PREWALL_URL = build_utm_url("/dashboard/ai-edit", "prewall")
TERMS_URL = build_utm_url("/terms-of-sale", "settings_terms")
PRIVACY_URL = build_utm_url("/privacy-policy", "settings_privacy")
CONTACT_CALL_URL = "https://calendly.com/barbot-yvann/30min"
GUIDE_URL = "https://terra-lab.ai/blog/ai-edit-complete-guide"
MARKETPLACE_URL = "https://plugins.qgis.org/plugins/AI_Edit/"







_URL_MAX_CHARS = 500
_URL_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f-\x9f<>\"'\\]")

_EMAIL_MAX_CHARS = 254


def _is_safe_link(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > _URL_MAX_CHARS:
        return False
    if _URL_FORBIDDEN_RE.search(value):
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return False


    return (parts.scheme == "https" and "." in host and not parts.username
            and not parts.password and (port is None or 1 <= port <= 65535))


def is_safe_link(value) -> bool:

    return _is_safe_link(value)


def _server_url(key: str, fallback: str) -> str:

    value = get_server_config().get(key)
    return value if _is_safe_link(value) else fallback


def get_server_url(key: str, fallback: str) -> str:






    return _server_url(key, fallback)


def get_terms_url(fallback: str = TERMS_URL) -> str:
    return _server_url("terms_url", fallback)


def get_privacy_url(fallback: str = PRIVACY_URL) -> str:
    return _server_url("privacy_url", fallback)


def get_contact_call_url() -> str:
    return _server_url("contact_call_url", CONTACT_CALL_URL)


def get_guide_url() -> str:
    return _server_url("guide_url", GUIDE_URL)


def get_cross_promo_url(fallback: str) -> str:
    return _server_url("cross_promo_url", fallback)


def _is_safe_email(value) -> bool:








    if not isinstance(value, str) or len(value) > _EMAIL_MAX_CHARS:
        return False
    value = value.strip()
    if not value or _URL_FORBIDDEN_RE.search(value):
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "@" not in domain and "." in domain.strip(".")


def get_support_email(fallback: str) -> str:
    value = get_server_config().get("support_email")
    return value.strip() if _is_safe_email(value) else fallback


def is_feature_enabled(name: str) -> bool:


    features = get_server_config().get("features")
    if not isinstance(features, dict):
        return True
    return features.get(name) is not False



_MAX_FEATURE_NOTE_CHARS = 200


def feature_disabled_message(name: str, fallback: str) -> str:




    notes = get_server_config().get("feature_notes")
    if not isinstance(notes, dict):
        return fallback
    value = notes.get(name)
    if not isinstance(value, str) or not value.strip():
        return fallback
    return value.strip()[:_MAX_FEATURE_NOTE_CHARS]



_MAX_UPDATE_LINE_CHARS = 160


def _served_update_line(key: str) -> str | None:


    value = get_server_config().get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:_MAX_UPDATE_LINE_CHARS]


def served_release_notes_line() -> str | None:
    return _served_update_line("release_notes_line")


def served_update_message() -> str | None:
    return _served_update_line("update_message")


def served_marketplace_url() -> str:
    return _server_url("marketplace_url", MARKETPLACE_URL)









DEFAULT_CONFIG = {
    "free_credits": 60,
    "free_tier_active": True,
    "upgrade_url": build_utm_url("/dashboard/ai-edit", "upgrade"),
}










def get_activation_key(settings=None) -> str:
    from .auth_helper import get_activation_key as _impl
    return _impl(settings)









_consent_memo: bool | None = None


def has_consent(settings=None) -> bool:




    global _consent_memo
    if settings is not None:
        return settings.value(f"{SETTINGS_PREFIX}consent_accepted", False, type=bool)
    if _consent_memo is None:
        from qgis.core import QgsSettings
        _consent_memo = bool(
            QgsSettings().value(f"{SETTINGS_PREFIX}consent_accepted", False, type=bool)
        )
    return _consent_memo


def save_consent(settings=None):





    global _consent_memo
    if settings is not None:
        settings.setValue(f"{SETTINGS_PREFIX}consent_accepted", True)
        return
    from qgis.core import QgsSettings
    QgsSettings().setValue(f"{SETTINGS_PREFIX}consent_accepted", True)
    _consent_memo = True







PRIVACY_NOTICE_SEEN_KEY = f"{SETTINGS_PREFIX}privacy_notice_seen"


def has_seen_privacy_notice() -> bool:
    from qgis.core import QgsSettings
    return bool(QgsSettings().value(PRIVACY_NOTICE_SEEN_KEY, False, type=bool))


def mark_privacy_notice_seen() -> None:

    from qgis.core import QgsSettings
    QgsSettings().setValue(PRIVACY_NOTICE_SEEN_KEY, True)


def save_activation(key: str, settings=None):

    from .auth_helper import save_activation as _impl
    _impl(key, settings)


def clear_activation(settings=None):

    from .auth_helper import clear_activation as _impl
    _impl(settings)


def migrate_legacy_key(settings=None) -> bool:

    from .auth_helper import migrate_legacy_key as _impl
    return _impl(settings)


def validate_key_with_server(client, key: str) -> tuple[bool, str, str, dict | None]:







    key = key.strip() if isinstance(key, str) else ""
    if not key:
        return (
            False,
            get_export_copy(
                "pipeline.activation_manager.enter_key", tr("Please enter your activation key.")
            ),
            "NO_KEY",
            None,
        )

    if not _KEY_RE.match(key):
        return False, get_export_copy(
            "pipeline.activation_manager.invalid_key_format",
            tr(
                "That does not look like an activation key. Most people do not need "
                "one: just use the Sign in button. A key starts with tl_ and is only "
                "for admin-issued or offline activation."
            ),
        ), "INVALID_FORMAT", None


    auth = {
        "Authorization": f"Bearer {key}",
        "X-Product-ID": "ai-edit",
    }
    try:
        result = client.get_usage(auth=auth)
    except Exception:
        return (
            False,
            get_export_copy(
                "pipeline.activation_manager.no_connection",
                tr("Cannot reach server. Check your internet connection."),
            ),
            "NO_CONNECTION",
            None,
        )

    if not isinstance(result, dict):
        return False, tr("Unexpected response from the server. Please try again."), "VALIDATION_FAILED", None

    if "error" in result:
        code = str(result.get("code") or "").strip().upper()
        error_msg = str(result.get("error") or get_export_copy(
            "pipeline.activation_manager.validation_failed", tr("Validation failed.")
        ))

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
                get_export_copy(
                    "pipeline.activation_manager.invalid_key",
                    tr("Invalid activation key. Check your key and try again."),
                ),
                code,
                None,
            )
        if code == "SUBSCRIPTION_INACTIVE":
            return (
                False,
                get_export_copy(
                    "pipeline.activation_manager.subscription_inactive",
                    tr(
                        "Your subscription has expired or been canceled. Renew at terra-lab.ai/dashboard"  # noqa: E501
                    ),
                ),
                code,
                None,
            )
        return False, error_msg, (code or "VALIDATION_FAILED"), None

    server_product = result.get("product_id", "")
    if server_product and server_product != "ai-edit":
        return (
            False,
            get_export_copy(
                "pipeline.activation_manager.wrong_product",
                tr("This key belongs to a different product. Use your AI Edit key."),
            ),
            "WRONG_PRODUCT",
            None,
        )

    return True, get_export_copy("pipeline.activation_manager.key_verified", tr("Activation key verified!")), "", result


def get_subscribe_url() -> str:
    return _server_url("subscribe_url", SUBSCRIBE_URL)


def get_dashboard_url() -> str:
    return _server_url("dashboard_url", DASHBOARD_URL)


def get_wall_url() -> str:
    return _server_url("wall_url", WALL_URL)


def get_prewall_url() -> str:
    return _server_url("prewall_url", PREWALL_URL)


def get_tutorial_url(client=None) -> str:







    url = get_server_config(client).get("tutorial_url")
    return url if _is_safe_link(url) else get_guide_url()





def get_server_config(client=None) -> dict:









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
        if isinstance(result, dict) and result and "error" not in result:
            if store is not None:
                store.set_activation_config(result)
            return result
    except Exception:
        pass  # nosec B110

    return DEFAULT_CONFIG


def clear_config_cache():

    from ..config_store import get_store
    store = get_store()
    if store is not None:
        store.clear_activation_config()

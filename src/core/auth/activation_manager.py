"""Activation manager for the AI Edit plugin.

Validates activation keys against the TerraLab backend.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..i18n import tr

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
# Free-tier paywall CTAs (wall screen / pre-wall banner): same dashboard
# path as every other subscribe CTA, only utm_content differs, so the
# funnel can tell which surface converted.
WALL_URL = build_utm_url("/dashboard/ai-edit", "wall")
PREWALL_URL = build_utm_url("/dashboard/ai-edit", "prewall")
TERMS_URL = build_utm_url("/terms-of-sale", "settings_terms")
PRIVACY_URL = build_utm_url("/privacy-policy", "settings_privacy")
CONTACT_CALL_URL = "https://calendly.com/barbot-yvann/30min"
GUIDE_URL = "https://terra-lab.ai/blog/ai-edit-complete-guide"


# A served link goes straight into a rich-text anchor, and several of them send
# someone to a payment page, so a prefix check is not enough. It must parse as
# https with a real host, and carry no control character, space, quote, angle
# bracket or backslash: those are the characters that would let a value break
# out of the href it is written into. Anything else keeps the shipped link.
_URL_MAX_CHARS = 500
_URL_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f-\x9f<>\"'\\]")
# An address, not a page: the longest one the mail standards allow.
_EMAIL_MAX_CHARS = 254


def _is_safe_link(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > _URL_MAX_CHARS:
        return False
    if _URL_FORBIDDEN_RE.search(value):
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
    except ValueError:
        return False
    # A dot in the host keeps out "https://localhost" and bare single labels,
    # which cannot be a TerraLab page.
    return parts.scheme == "https" and "." in host


def _server_url(key: str, fallback: str) -> str:
    """Cache-only server override for a URL; the shipped constant otherwise."""
    value = get_server_config().get(key)
    return value if _is_safe_link(value) else fallback


def get_server_url(key: str, fallback: str) -> str:
    """Server override for any link, the shipped constant otherwise.

    The named accessors below cover the links this module owns. Call sites
    that keep their own shipped constant (the error-path links) route through
    here, so every link in the plugin resolves the same way and none of them
    needs a release to change."""
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
    """A served support address, checked like a served link.

    "@ and no space" is not enough: the address is written into a rich-text
    label (the Contact dialog shows it in bold), so a value carrying an angle
    bracket could open a tag there, and one carrying a quote or a newline could
    break out of the mailto: URL the send button builds. Exactly one "@" and a
    dotted domain after it, and none of the characters a link may not have.
    """
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
    """Server kill switch. Fail-open: absent map, absent key, or garbage all
    mean enabled; only an explicit false disables."""
    features = get_server_config().get("features")
    if not isinstance(features, dict):
        return True
    return features.get(name) is not False


# A note is one line under a hidden control, not a page of copy.
_MAX_FEATURE_NOTE_CHARS = 200


def feature_disabled_message(name: str, fallback: str) -> str:
    """Why a switched-off feature is off, and for how long, in the server's
    own words. Lets an incident say something better than "try again later"
    without a release. Falls back to the shipped sentence whenever the server
    says nothing usable; the returned text is plain, never markup."""
    notes = get_server_config().get("feature_notes")
    if not isinstance(notes, dict):
        return fallback
    value = notes.get(name)
    if not isinstance(value, str) or not value.strip():
        return fallback
    return value.strip()[:_MAX_FEATURE_NOTE_CHARS]


def parse_version(text) -> tuple[int, ...] | None:
    """'1.6.9' -> (1, 6, 9); None on anything that is not dotted ints."""
    if not isinstance(text, str):
        return None
    parts = text.strip().lstrip("vV").split(".")
    out = []
    for part in parts:
        part = part.strip()
        if not part.isdigit():
            return None
        out.append(int(part))
    return tuple(out) if out else None


def is_update_recommended(installed_version: str) -> bool:
    """True when the server's min_recommended_version parses strictly higher
    than the installed version. Garbage or missing on either side = False."""
    installed = parse_version(installed_version)
    minimum = parse_version(get_server_config().get("min_recommended_version"))
    if installed is None or minimum is None:
        return False
    width = max(len(installed), len(minimum))
    installed += (0,) * (width - len(installed))
    minimum += (0,) * (width - len(minimum))
    return minimum > installed


# Hardcoded fallback config (used when server is unreachable).
# free_credits mirrors the server's monthly free allowance in CREDITS
# (200 credits = 10 generations per month, renewed on the 1st).
DEFAULT_CONFIG = {
    "free_credits": 200,
    "free_tier_active": True,
    "upgrade_url": build_utm_url("/dashboard/ai-edit", "upgrade"),
}


# qgis imports stay function-local below: this module's pure half
# (key regex, server validation, URLs) is exercised by the headless test
# layer, which runs without QGIS installed.

# The four key functions below are the surface the ui layer imports; auth_helper
# holds the storage, and it needs qgis.core at import time, so it cannot be the
# surface. Same-named pairs here and there are that facade, not a duplicate.

def get_activation_key(settings=None) -> str:
    from .auth_helper import get_activation_key as _impl
    return _impl(settings)


# Session memo of the terms/privacy flag. It is read on EVERY keystroke in the
# prompt box (dock/prompts._update_generate_enabled) and a QgsSettings
# construction costs ~89 us against a real profile, against ~1 us to read a
# value. It stands for ONE store, the QGIS profile, so only a write to that
# store may set it. Invalidation rule: `save_consent` with no argument is the
# only writer in the plugin and it refreshes the memo; `reset_consent_memo`
# is the hook for anything that rewrites the profile behind our back, and
# tests/test_auth.py exercises it. Consent is one-way (never revoked in code),
# so a stale True is not reachable.
_consent_memo: bool | None = None


def has_consent(settings=None) -> bool:
    """Check if the user has accepted terms and privacy policy.

    An explicit `settings` reads that store directly and never touches the
    memo, so a caller holding its own QgsSettings still gets what is in it."""
    global _consent_memo
    if settings is not None:
        return settings.value(f"{SETTINGS_PREFIX}consent_accepted", False, type=bool)
    if _consent_memo is None:
        from qgis.core import QgsSettings
        _consent_memo = bool(
            QgsSettings().value(f"{SETTINGS_PREFIX}consent_accepted", False, type=bool)
        )
    return _consent_memo


def reset_consent_memo() -> None:
    """Force the next `has_consent()` back to QgsSettings."""
    global _consent_memo
    _consent_memo = None


def save_consent(settings=None):
    """Mark that the user accepted terms and privacy policy.

    Only a write to the QGIS profile touches the memo. An explicit `settings`
    writes that store and leaves the memo alone: a scratch store saying yes is
    no evidence that the profile did, and this flag gates a legal consent."""
    global _consent_memo
    if settings is not None:
        settings.setValue(f"{SETTINGS_PREFIX}consent_accepted", True)
        return
    from qgis.core import QgsSettings
    QgsSettings().setValue(f"{SETTINGS_PREFIX}consent_accepted", True)
    _consent_memo = True


def save_activation(key: str, settings=None):
    """Save activation key (encrypted via QgsAuthManager when available)."""
    from .auth_helper import save_activation as _impl
    _impl(key, settings)


def clear_activation(settings=None):
    """Clear activation state and the activation timestamp cohort marker."""
    from .auth_helper import clear_activation as _impl
    _impl(settings)


def migrate_legacy_key(settings=None) -> bool:
    """Migrate any QSettings-only key to QgsAuthManager. Idempotent."""
    from .auth_helper import migrate_legacy_key as _impl
    return _impl(settings)


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
    return _server_url("subscribe_url", SUBSCRIBE_URL)


def get_dashboard_url() -> str:
    return _server_url("dashboard_url", DASHBOARD_URL)


def get_wall_url() -> str:
    return _server_url("wall_url", WALL_URL)


def get_prewall_url() -> str:
    return _server_url("prewall_url", PREWALL_URL)


def get_tutorial_url(client=None) -> str:
    """The tutorial link, or the guide when the server has nothing usable.

    Checked like every other served link (see _is_safe_link): the value goes
    into a rich-text anchor in the message bar and into an external-browser
    open, so plain http and any character that could break out of an href keep
    the shipped guide instead.
    """
    url = get_server_config(client).get("tutorial_url")
    return url if _is_safe_link(url) else get_guide_url()


# -- Server config (delegates to ConfigStore so unload + Plugin Reloader stay clean) --


def get_server_config(client=None) -> dict:
    """The config in force, or the shipped defaults.

    Cache first: the store keeps whatever the startup fetch returned for the
    whole session, so every reader above answers the same way from the first
    click to the last. A ``client`` is only used when nothing has been fetched
    yet, and its answer has to be a dict: the client hands back whatever valid
    JSON arrived, so a list or a string can get this far, and the readers all
    call ``.get`` on what comes out of here.
    """
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
    """Clear cached config (e.g. on plugin reload)."""
    from ..config_store import get_store
    store = get_store()
    if store is not None:
        store.clear_activation_config()

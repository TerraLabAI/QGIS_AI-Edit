"""Who is asking, for the startup calls that can answer differently per client.

The server needs this to send copy in the user's language and to reach the
users on one broken version without touching anyone else. Four values, all
optional: a missing or unreadable one is simply left out, and nothing here may
ever make a request fail.

Nothing identifying: a language, a plugin version, a QGIS version and an
operating system name, the same four a plugin listing already shows.
"""
from __future__ import annotations

import os
import re

# The languages the server writes copy in. A QGIS locale outside this list
# sends no lang at all, and the server picks its own default.
REQUEST_LANGS: tuple[str, ...] = ("en", "fr", "es", "pt_BR")

# Values go into a query string, so keep them to characters that survive one
# unescaped: letters, digits, dot, dash, underscore.
_SAFE_VALUE_RE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_VALUE_CHARS = 24


def _sanitize(value: str) -> str:
    """Trim a value to something safe to put in a query string. Empty when
    nothing usable survives, which means the caller omits the parameter."""
    if not isinstance(value, str):
        return ""
    return _SAFE_VALUE_RE.sub("", value.strip())[:_MAX_VALUE_CHARS]


def request_lang() -> str:
    """The user's language, as one of REQUEST_LANGS, or "" when the QGIS
    locale maps to none of them."""
    try:
        from .i18n import _read_user_locale

        locale = (_read_user_locale() or "").replace("-", "_")
    except Exception:  # nosec B110
        return ""
    if not isinstance(locale, str) or not locale:
        return ""
    lowered = locale.lower()
    # Region-qualified first, so pt_PT does not answer as pt_BR.
    for lang in REQUEST_LANGS:
        if "_" in lang and lowered.startswith(lang.lower()):
            return lang
    short = lowered[:2]
    if short == "pt":
        # Brazilian Portuguese is the only Portuguese the plugin ships.
        return "pt_BR" if "pt_BR" in REQUEST_LANGS else ""
    for lang in REQUEST_LANGS:
        if "_" not in lang and lang == short:
            return lang
    return ""


def plugin_version() -> str:
    """Version= from metadata.txt, "" when it cannot be read."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        with open(os.path.join(root, "metadata.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass  # nosec B110 - the version is context, never a reason to fail
    return ""


def _qgis_version() -> str:
    """QGIS version number, without the release name."""
    try:
        from qgis.core import Qgis

        return str(Qgis.QGIS_VERSION).split("-")[0].strip()
    except Exception:  # nosec B110
        return ""


def _os_name() -> str:
    try:
        import platform

        return platform.system()
    except Exception:  # nosec B110
        return ""


def request_context() -> dict[str, str]:
    """The context parameters to send, already sanitised. A value that cannot
    be worked out is absent from the dict, never blank."""
    candidates = {
        "lang": request_lang,
        "v": plugin_version,
        "qgis": _qgis_version,
        "os": _os_name,
    }
    out: dict[str, str] = {}
    for key, read in candidates.items():
        try:
            value = _sanitize(read())
        except Exception:  # nosec B112 - continue, so B112 not B110
            continue
        if value:
            out[key] = value
    return out

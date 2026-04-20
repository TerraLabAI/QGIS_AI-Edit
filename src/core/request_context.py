









from __future__ import annotations

import os
import re
import sys



REQUEST_LANGS: tuple[str, ...] = ("en", "fr", "es", "pt_BR")



_SAFE_VALUE_RE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_VALUE_CHARS = 24


def _sanitize(value: str) -> str:


    if not isinstance(value, str):
        return ""
    return _SAFE_VALUE_RE.sub("", value.strip())[:_MAX_VALUE_CHARS]


def request_lang() -> str:


    try:
        from .i18n import _read_user_locale

        locale = (_read_user_locale() or "").replace("-", "_")
    except Exception:  # nosec B110
        return ""
    if not isinstance(locale, str) or not locale:
        return ""
    lowered = locale.lower()

    for lang in REQUEST_LANGS:
        if "_" in lang and lowered.startswith(lang.lower()):
            return lang
    short = lowered[:2]
    if short == "pt":

        return "pt_BR" if "pt_BR" in REQUEST_LANGS else ""
    for lang in REQUEST_LANGS:
        if "_" not in lang and lang == short:
            return lang
    return ""


def plugin_version() -> str:

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        with open(os.path.join(root, "metadata.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass  # nosec B110
    return ""


def _qgis_version() -> str:

    try:
        from qgis.core import Qgis

        return str(Qgis.QGIS_VERSION).split("-")[0].strip()
    except Exception:  # nosec B110
        return ""


def _os_name() -> str:


    if sys.platform == "win32":
        return "Windows"
    try:
        return os.uname().sysname
    except Exception:  # nosec B110
        return ""


def request_context() -> dict[str, str]:


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
        except Exception:  # nosec B112
            continue
        if value:
            out[key] = value
    return out

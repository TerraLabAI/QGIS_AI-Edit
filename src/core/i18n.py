"""
Internationalization (i18n) support for AI Edit plugin.

Parses .ts XML files directly at runtime - no binary .qm files needed.
This ensures compliance with QGIS plugin repository rules (no binaries).

Security: Uses defusedxml for safe XML parsing (no global monkey-patch).
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET  # nosec B405

# Prefer defusedxml for safe XML parsing (no global monkey-patch)
try:
    from defusedxml.ElementTree import parse as _safe_parse
except ImportError:
    _safe_parse = ET.parse  # fallback: .ts files are local trusted plugin files

# Translation context - must match the context in .ts files
CONTEXT = "AIEdit"

# Translation dictionary: {source_text: translated_text}
_translations: dict[str, str] = {}

# Flag to track if translations have been loaded
_loaded = False

# Session memo of the locale string. QGIS only applies a locale change after a
# restart, so this cannot move under us. It matters because constructing a
# QSettings costs ~82 us against a real profile while reading a value off an
# existing one costs ~1 us, and the prompt catalog resolves a polyglot label
# hundreds of times per build (see prompt_presets._pick_label).
_user_locale: str | None = None


def _query_user_locale() -> str:
    """QGIS locale setting, read fresh; English when QGIS is not available
    (headless tests)."""
    try:
        from qgis.PyQt.QtCore import QSettings
    except ImportError:
        return "en_US"
    return QSettings().value("locale/userLocale", "en_US")


def _read_user_locale() -> str:
    """The QGIS locale, read once per session (see `_user_locale`)."""
    global _user_locale
    if _user_locale is None:
        _user_locale = _query_user_locale()
    return _user_locale


def reset_locale_cache() -> None:
    """Drop the cached locale AND the translations loaded from it.

    Invalidation rule: the trio is valid for as long as the QGIS locale is,
    which is the whole session. Call this only when the locale itself changes
    under the plugin (a future in-session language switch); everything derived
    from it is rebuilt on the next read. tests/test_i18n.py drives it through a
    language change and checks that the translations follow."""
    global _user_locale, _loaded
    _user_locale = None
    _loaded = False
    _translations.clear()


def _load_translations():
    """Load translations from .ts XML file based on QGIS locale."""
    global _loaded

    if _loaded:
        return

    _loaded = True

    # Get the locale from QGIS settings
    locale = _read_user_locale()
    if not locale:
        return

    # English is the source language - no translation needed
    if locale.startswith("en"):
        return

    # Find the translation file
    plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Language fallbacks: map language variants to available translations
    language_fallbacks = {
        "pt": "pt_BR",
        "pt_PT": "pt_BR",
        "es_MX": "es",
        "es_AR": "es",
        # Chinese: route bare/script/region variants to Simplified or Traditional.
        # Order in locale_variants ensures Hant/HK/MO resolve to zh_TW before the
        # generic "zh" -> zh_CN fallback is reached.
        "zh": "zh_CN",
        "zh_Hans": "zh_CN",
        "zh_SG": "zh_CN",
        "zh_Hant": "zh_TW",
        "zh_HK": "zh_TW",
        "zh_MO": "zh_TW",
    }

    locale_variants = []
    normalized_locale = locale.replace("-", "_")

    if "_" in normalized_locale:
        locale_variants.append(normalized_locale)
        locale_variants.append(normalized_locale[:2])
        if normalized_locale in language_fallbacks:
            locale_variants.append(language_fallbacks[normalized_locale])
        if normalized_locale[:2] in language_fallbacks:
            locale_variants.append(language_fallbacks[normalized_locale[:2]])
    else:
        locale_variants.append(normalized_locale[:2])
        if normalized_locale[:2] in language_fallbacks:
            locale_variants.append(language_fallbacks[normalized_locale[:2]])

    ts_path = None
    for variant in locale_variants:
        candidate = os.path.join(plugin_dir, "i18n", f"ai_edit_{variant}.ts")
        if os.path.exists(candidate):
            ts_path = candidate
            break

    if ts_path is None:
        return

    try:
        tree = _safe_parse(ts_path)
        root = tree.getroot()

        for context in root.findall("context"):
            context_name = context.find("name")
            if context_name is None or context_name.text != CONTEXT:
                continue

            for message in context.findall("message"):
                source = message.find("source")
                translation = message.find("translation")

                if source is None or translation is None:
                    continue

                source_text = source.text or ""
                translation_text = translation.text

                # Skip unfinished/empty translations
                if translation_text and translation.get("type") != "unfinished":
                    _translations[source_text] = translation_text

    except Exception as e:
        try:
            from qgis.core import Qgis, QgsMessageLog
            QgsMessageLog.logMessage(
                f"Failed to load translations from {ts_path}: {e}",
                "AI Edit",
                level=Qgis.MessageLevel.Warning
            )
        except Exception:
            pass  # nosec B110


def tr(message: str) -> str:
    """
    Translate a string using the plugin's translation files.

    Args:
        message: The string to translate (English source text)

    Returns:
        The translated string, or the original if no translation is available
    """
    if not _loaded:
        _load_translations()

    return _translations.get(message, message)


def get_locale() -> str:
    """Get the 2-letter language code from QGIS settings."""
    locale = _read_user_locale()
    if locale:
        return locale[:2]
    return "en"

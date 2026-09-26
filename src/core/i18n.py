







from __future__ import annotations

import os
import re
import threading
import xml.etree.ElementTree as ET  # nosec B405


try:
    from defusedxml.ElementTree import parse as _safe_parse
except ImportError:
    def _safe_parse(path):


        with open(path, encoding="utf-8-sig") as source:
            text = source.read().replace("<!DOCTYPE TS>", "")
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            raise ValueError("Translation XML declarations are not allowed")
        return ET.ElementTree(ET.fromstring(text))  # nosec B314


CONTEXT = "AIEdit"


_translations: dict[str, str] = {}


_loaded = False
_locale_lock = threading.RLock()






_user_locale: str | None = None


def _query_user_locale() -> str:


    try:
        from qgis.PyQt.QtCore import QSettings
    except ImportError:
        return "en_US"
    try:
        return QSettings().value("locale/userLocale", "en_US")
    except (TypeError, ValueError, RuntimeError):
        return "en_US"


def _read_user_locale() -> str:

    global _user_locale
    with _locale_lock:
        if _user_locale is None:
            raw = _query_user_locale()
            value = raw.strip().split(".", 1)[0].split("@", 1)[0].replace("-", "_") if isinstance(raw, str) else ""
            if not re.fullmatch(r"[A-Za-z]{2,3}(?:_[A-Za-z0-9]{2,8})*", value):
                value = "en_US"
            parts = value.split("_")
            _user_locale = "_".join([
                parts[0].lower(),
                *(p.title() if len(p) == 4 else p.upper() for p in parts[1:]),
            ])
        return _user_locale


def _load_translations():

    global _loaded
    with _locale_lock:
        if not _loaded:
            try:
                _load_translation_file()
            finally:
                _loaded = True


def _load_translation_file():

    locale = _read_user_locale()
    if not locale:
        return


    if locale.startswith("en"):
        return


    plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


    language_fallbacks = {
        "pt": "pt_BR",
        "pt_PT": "pt_BR",
        "es_MX": "es",
        "es_AR": "es",



        "zh": "zh_CN",
        "zh_Hans": "zh_CN",
        "zh_SG": "zh_CN",
        "zh_Hant": "zh_TW",
        "zh_HK": "zh_TW",
        "zh_MO": "zh_TW",
    }

    locale_variants = [locale]
    parts = locale.split("_")

    for width in range(len(parts), 0, -1):
        variant = "_".join(parts[:width])
        alias = language_fallbacks.get(variant)
        if alias and alias not in locale_variants:
            locale_variants.append(alias)
    if parts[0] not in locale_variants:
        locale_variants.append(parts[0])

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

        translations = {}
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


                if (
                    source_text and translation_text
                    and translation.get("type") not in ("unfinished", "obsolete", "vanished")
                ):
                    translations[source_text] = translation_text
        _translations.update(translations)

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









    if not _loaded:
        _load_translations()

    return _translations.get(message, message)


def get_locale() -> str:

    locale = _read_user_locale()
    if locale:
        return locale[:2]
    return "en"

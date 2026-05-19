
from __future__ import annotations

from qgis.PyQt.QtCore import QLocale

from .qt_compat import LocaleDefaultNumberOptions as _DEFAULT_OPTIONS


def format_count(value) -> str:








    if isinstance(value, bool) or not isinstance(value, int):
        return str(value)
    locale = QLocale()
    locale.setNumberOptions(_DEFAULT_OPTIONS)
    return locale.toString(value)

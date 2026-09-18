"""Counts as people read them: "99,420", or "99 420" on a French QGIS."""
from __future__ import annotations

from qgis.PyQt.QtCore import QLocale

from .qt_compat import LocaleDefaultNumberOptions as _DEFAULT_OPTIONS


def format_count(value) -> str:
    """Group the digits of a whole number with the QGIS locale's separator.

    QGIS turns grouping off in its default locale unless the user opted in,
    which suits coordinates in a table but not a balance of credits, so the
    default options, grouping included, are put back here. Anything that is
    not an int is returned as ``str(value)``, so a caller never has to guard
    a missing balance.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return str(value)
    locale = QLocale()
    locale.setNumberOptions(_DEFAULT_OPTIONS)
    return locale.toString(value)

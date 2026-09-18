
from __future__ import annotations

import sys

from qgis.PyQt.QtCore import QLocale
from qgis.PyQt.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)



_TEXT_INPUT_CLASSES = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
_EURO_ON_ALTGR_E_LANGUAGES = frozenset(
    getattr(QLocale.Language, name)
    for name in ("French", "German", "Spanish", "Italian", "Portuguese", "Catalan")
)


def focus_is_text_input(widget) -> bool:

    if widget is None:
        return False
    if isinstance(widget, _TEXT_INPUT_CLASSES):
        return True
    return isinstance(widget, QComboBox) and widget.isEditable()


def insert_euro_if_layout_types_it(widget) -> None:


    try:
        language = QApplication.inputMethod().locale().language()
    except (AttributeError, RuntimeError):
        return
    if language not in _EURO_ON_ALTGR_E_LANGUAGES:
        return
    if isinstance(widget, QComboBox):
        widget = widget.lineEdit()
    if isinstance(widget, QLineEdit):
        if not widget.isReadOnly():
            widget.insert("€")
    elif isinstance(widget, (QTextEdit, QPlainTextEdit)):
        if not widget.isReadOnly():
            widget.insertPlainText("€")


class LaunchShortcutMixin:
    def _on_launch_shortcut_key(self):






        if sys.platform == "win32":
            focus = QApplication.focusWidget()
            if focus_is_text_input(focus):
                insert_euro_if_layout_types_it(focus)
                return
        self._on_launch_shortcut()

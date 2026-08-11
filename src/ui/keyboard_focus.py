










from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAbstractButton, QPushButton, QWidget


def apply_keyboard_focus_policy(root: QWidget | None) -> None:





    if root is None:
        return
    try:
        buttons = root.findChildren(QAbstractButton)
    except RuntimeError:
        return
    if isinstance(root, QAbstractButton):
        buttons.append(root)
    for button in buttons:
        if button.focusPolicy() in (Qt.FocusPolicy.StrongFocus, Qt.FocusPolicy.ClickFocus,
                                    Qt.FocusPolicy.WheelFocus):
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)


def settle_dialog_default_button(dialog: QWidget | None, primary: QPushButton | None = None) -> None:




    if dialog is None:
        return
    try:
        buttons = dialog.findChildren(QPushButton)
    except RuntimeError:
        return
    for button in buttons:
        if button is primary:
            continue
        button.setDefault(False)
        button.setAutoDefault(False)
    if primary is not None:
        primary.setAutoDefault(True)
        primary.setDefault(True)
    apply_keyboard_focus_policy(dialog)


__all__ = ["apply_keyboard_focus_policy", "settle_dialog_default_button"]

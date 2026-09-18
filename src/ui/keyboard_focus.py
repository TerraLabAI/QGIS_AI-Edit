"""Keyboard focus for AI Edit's panels and dialogs, as in AI Segmentation.

The focus ring (the blue ``ACCENT_BORDER`` in ``dock/design_tokens.py``) is
for the keyboard only. Buttons take focus by Tab alone (``Qt.TabFocus``), so a
mouse click never leaves a ring behind and ``:focus`` only ever matches a
keyboard focus.

Enter fires a dialog's one filled action and nothing else: every other push
button leaves ``autoDefault`` off, or ``QDialog`` makes the first of them the
default when it opens.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAbstractButton, QPushButton, QWidget


def apply_keyboard_focus_policy(root: QWidget | None) -> None:
    """Buttons under ``root`` take focus from Tab only.

    ``NoFocus`` stays as it is: a button kept out of the tab chain on purpose
    (an overlay shown on hover) must not join it.
    """
    if root is None:
        return
    try:
        buttons = root.findChildren(QAbstractButton)
    except RuntimeError:
        return  # the widget was deleted before the sweep
    if isinstance(root, QAbstractButton):
        buttons.append(root)
    for button in buttons:
        if button.focusPolicy() in (Qt.FocusPolicy.StrongFocus, Qt.FocusPolicy.ClickFocus,
                                    Qt.FocusPolicy.WheelFocus):
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)


def settle_dialog_default_button(dialog: QWidget | None, primary: QPushButton | None = None) -> None:
    """Enter fires ``primary`` alone; no other push button can become default.

    Also applies the Tab-only focus policy to the dialog's buttons.
    """
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

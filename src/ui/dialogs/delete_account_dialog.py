"""Typed-confirmation dialog for deleting a TerraLab account.

The account dialog owns the network call; this file owns the moment where the
user is told exactly what is about to happen and has to retype their own email
address before the destructive button becomes clickable at all.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr


class DeleteAccountDialog(QDialog):
    """Ask for the account email in writing before scheduling the erasure.

    The dialog only collects the confirmation: it returns the typed text and
    the caller decides whether to send it. Accepting is impossible until the
    field matches the account address, so a mistyped confirmation never even
    reaches the server.
    """

    def __init__(self, email: str, parent=None):
        from .confirm_dialog import CONFIRM_WINDOW_TITLE

        super().__init__(parent)
        # The product in the title bar and the question inside, as in every
        # confirm window, never the same words twice.
        self.setWindowTitle(CONFIRM_WINDOW_TITLE)
        self.setObjectName("confirmDialog")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(520)

        self._email = str(email or "").strip()
        self._setup_ui()

    def _setup_ui(self):
        # Button styles are imported lazily for the same reason the error
        # report dialog does it: a module-top import of ..dock.style pulls the
        # dock package __init__, which imports back into this package.
        from ..dock.design_tokens import (
            BTN_GHOST_QSS,
            FONT_BASE,
            FONT_BODY,
            INK,
            INK_2,
            INPUT_QSS,
            LINE,
        )
        from .confirm_dialog import DANGER_FILLED_QSS, DIALOG_SURFACE_QSS

        self.setStyleSheet(DIALOG_SURFACE_QSS)

        heading_qss = f"font-size: 15px; font-weight: 600; color: {INK}; background: transparent;"
        body_qss = f"font-size: {FONT_BODY}px; color: {INK}; background: transparent;"
        muted_qss = f"font-size: {FONT_BODY}px; color: {INK_2}; background: transparent;"

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 22, 24, 18)

        heading = QLabel(get_export_copy(
            "dialogs.delete_account_dialog.heading", tr("Delete your account?")))
        heading.setWordWrap(True)
        heading.setStyleSheet(heading_qss)
        layout.addWidget(heading)

        # The consequences, one line each, in the order they hit the user: the
        # data, then the tools that stop working, then the money.
        consequences = (
            get_export_copy(
                "dialogs.delete_account_dialog.consequence_data",
                tr("Your generations, history and prompts are erased.")),
            get_export_copy(
                "dialogs.delete_account_dialog.consequence_tools",
                tr("Every TerraLab plugin stops, on all computers.")),
            get_export_copy(
                "dialogs.delete_account_dialog.consequence_billing",
                tr("Any subscription stops renewing.")),
        )
        # One block, the lines close together: at the window's 12 px each
        # bullet read as its own paragraph.
        bullets = QVBoxLayout()
        bullets.setSpacing(4)
        for line in consequences:
            item = QLabel("\u2022  " + line)
            item.setWordWrap(True)
            item.setTextFormat(Qt.TextFormat.PlainText)
            item.setStyleSheet(body_qss)
            bullets.addWidget(item)
        layout.addLayout(bullets)

        grace = QLabel(get_export_copy(
            "dialogs.delete_account_dialog.grace_notice",
            tr("Cancel on terra-lab.ai during the grace period. After that, it is final.")
        ))
        grace.setWordWrap(True)
        grace.setTextFormat(Qt.TextFormat.PlainText)
        grace.setStyleSheet(muted_qss)
        layout.addWidget(grace)

        separator = QFrame()
        separator.setObjectName("deleteAccountRule")
        separator.setFixedHeight(1)
        separator.setStyleSheet(
            f"QFrame#deleteAccountRule {{ border: none; border-top: 1px solid {LINE}; }}")
        layout.addWidget(separator)

        prompt = QLabel(get_export_copy(
            "dialogs.delete_account_dialog.confirm_prompt",
            tr("Type your email to confirm:")))
        prompt.setWordWrap(True)
        prompt.setTextFormat(Qt.TextFormat.PlainText)
        prompt.setStyleSheet(body_qss)
        layout.addWidget(prompt)

        shown = QLabel(self._email or get_export_copy(
            "dialogs.delete_account_dialog.no_email", tr("(no email)")))
        shown.setTextFormat(Qt.TextFormat.PlainText)
        shown.setTextInteractionFlags(QtC.TextSelectableByMouse)
        shown.setStyleSheet(
            f"font-size: {FONT_BASE}px; font-weight: 600; color: {INK}; background: transparent;")
        layout.addWidget(shown)

        self._input = QLineEdit()
        self._input.setPlaceholderText(get_export_copy(
            "dialogs.delete_account_dialog.input_placeholder", tr("Your email")))
        self._input.setStyleSheet(INPUT_QSS)
        self._input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._input)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch()

        cancel_btn = QPushButton(get_export_copy(
            "dialogs.delete_account_dialog.cancel_button", tr("Cancel")))
        cancel_btn.setStyleSheet(BTN_GHOST_QSS)
        cancel_btn.setCursor(QtC.PointingHandCursor)
        # Cancel carries Return: on a screen whose other button erases an
        # account, the key pressed by reflex must be the harmless one.
        cancel_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        self._confirm_btn = QPushButton(get_export_copy(
            "dialogs.delete_account_dialog.delete_action", tr("Delete account")))
        # The confirm window's one red fill (ui.md): grey until the address
        # matches, so it cannot be mistaken for a live button.
        self._confirm_btn.setStyleSheet(DANGER_FILLED_QSS)
        self._confirm_btn.setCursor(QtC.PointingHandCursor)
        self._confirm_btn.setAutoDefault(False)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(self.accept)
        buttons.addWidget(self._confirm_btn)

        layout.addLayout(buttons)

    def _on_text_changed(self, _text: str) -> None:
        self._confirm_btn.setEnabled(self._matches())

    def _matches(self) -> bool:
        typed = self._input.text().strip().lower()
        return bool(self._email) and typed == self._email.strip().lower()

    def typed_text(self) -> str:
        """What the user actually typed, sent to the server as written."""
        return self._input.text().strip()


def show_delete_account(parent, email: str) -> str | None:
    """Open the confirmation dialog. Returns the typed address, or None.

    None covers every way of not going through with it: Cancel, Escape, or the
    window closed. The caller treats all of them the same way, which is to do
    nothing.
    """
    dialog = DeleteAccountDialog(email, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.typed_text()
    return None

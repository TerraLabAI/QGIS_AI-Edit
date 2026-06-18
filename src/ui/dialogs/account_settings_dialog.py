"""Account Settings dialog for AI Edit plugin."""
from __future__ import annotations

from datetime import datetime

from qgis.PyQt.QtCore import QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.auth.activation_manager import (
    get_dashboard_url,
    get_privacy_url,
    get_terms_url,
)
from ...core.i18n import tr
from ...workers.generic_request_task import GenericRequestTask
from ..dock_widget import (
    BRAND_BLUE,
    BRAND_BLUE_HOVER,
    BRAND_GREEN,
    BRAND_GREEN_TEXT,
    BRAND_RED,
)
from ..onboarding_hint import reset_hints
from ..raster_writer import get_output_dir, set_output_dir

PRODUCT_ID = "ai-edit"
PRODUCT_NAME = "AI Edit"

_STATUS_DISPLAY = {
    "active": (tr("Active"), BRAND_GREEN_TEXT),
    "trialing": (tr("Free Trial"), "#f57c00"),
    "canceled": (tr("Canceled"), BRAND_RED),
}

_LINK_BTN = (
    f"QPushButton {{ border: none; color: {BRAND_BLUE}; font-size: 11px;"
    f" text-decoration: underline; padding: 2px 4px; background: transparent; }}"
    f"QPushButton:hover {{ color: {BRAND_BLUE_HOVER}; }}"
)

# Prominent primary action: open the account dashboard on terra-lab.ai.
_MANAGE_BTN = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #ffffff;"
    f" border: none; border-radius: 8px; padding: 9px 16px;"
    f" font-size: 12px; font-weight: 600; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
)

# Compact sign-out link (sits inside the account chip, not a full-width button).
_SIGNOUT_LINK = (
    "QPushButton { border: none; background: transparent; color: palette(text);"
    " font-size: 11px; text-decoration: underline; padding: 2px 4px; }"
    f"QPushButton:hover {{ color: {BRAND_RED}; }}"
)

# Normal buttons inside a card need an explicit background, else the card's
# "QPushButton { background: transparent }" rule breaks native rendering.
_PREF_BTN = (
    "QPushButton { background: palette(button); color: palette(button-text);"
    " border: 1px solid rgba(128,128,128,0.45); border-radius: 5px;"
    " padding: 3px 12px; }"
    "QPushButton:hover { background: rgba(128,128,128,0.18); }"
)

_CARD_STYLE = (
    "QFrame { background: rgba(128,128,128,0.08);"
    " border: 1px solid rgba(128,128,128,0.2);"
    " border-radius: 6px; }"
    "QLabel { background: transparent; border: none; }"
    "QPushButton { background: transparent; }"
)

# Quiet local-preference rows (output folder, guidance) sit below the account
# cards. Aligned labels keep their controls on a shared left edge.
_PREF_LABEL_W = 132
_PREF_LABEL_STYLE = (
    "font-size: 11px; color: palette(text);"
    " background: transparent; border: none;"
)


class AccountSettingsDialog(QDialog):

    sign_out_requested = pyqtSignal()

    def __init__(self, client, auth, activation_key, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Account Settings"))
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)

        # The activation key now lives only in the web dashboard; Settings is
        # pure account management, so the key is intentionally not shown here.
        self._worker = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 12)
        self._layout.setSpacing(12)

        self._loading_label = QLabel(tr("Loading account info..."))
        self._loading_label.setAlignment(QtC.AlignCenter)
        self._loading_label.setStyleSheet("color: palette(text); padding: 16px;")
        self._layout.addWidget(self._loading_label)

        self._error_widget = QWidget()
        error_layout = QVBoxLayout(self._error_widget)
        error_layout.setContentsMargins(0, 0, 0, 0)
        error_layout.setSpacing(8)
        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setAlignment(QtC.AlignCenter)
        self._error_label.setStyleSheet(f"color: {BRAND_RED}; padding: 12px;")
        error_layout.addWidget(self._error_label)
        self._retry_btn = QPushButton(tr("Retry"))
        self._retry_btn.setMaximumWidth(100)
        self._retry_btn.clicked.connect(self._fetch_account)
        self._error_sign_out_btn = QPushButton(tr("Sign out"))
        self._error_sign_out_btn.setStyleSheet(_LINK_BTN)
        self._error_sign_out_btn.setCursor(QtC.PointingHandCursor)
        self._error_sign_out_btn.clicked.connect(self._on_sign_out)
        retry_row = QHBoxLayout()
        retry_row.addStretch()
        retry_row.addWidget(self._retry_btn)
        retry_row.addStretch()
        error_layout.addLayout(retry_row)
        change_row = QHBoxLayout()
        change_row.addStretch()
        change_row.addWidget(self._error_sign_out_btn)
        change_row.addStretch()
        error_layout.addLayout(change_row)
        self._error_widget.setVisible(False)
        self._layout.addWidget(self._error_widget)

        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(10)
        self._content_widget.setVisible(False)
        self._layout.addWidget(self._content_widget)

        self._layout.addStretch()

        self._client = client
        self._auth = auth
        self._fetch_account()

    def _fetch_account(self):
        self._loading_label.setVisible(True)
        self._error_widget.setVisible(False)
        self._content_widget.setVisible(False)

        from qgis.core import QgsApplication

        # Drop any previous in-flight load (Retry) so its result can't land late.
        self._cancel_worker()

        auth = self._auth
        client = self._client
        self._worker = GenericRequestTask(
            "AI Edit account load",
            lambda: client.get_account(auth=auth),
        )
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        QgsApplication.taskManager().addTask(self._worker)

    def _cancel_worker(self):
        """Disconnect then cancel the loader task so a late result never fires
        into a closed dialog. We never force-kill a thread mid network-call,
        which can corrupt Qt's socket state; cancellation is cooperative."""
        if self._worker is None:
            return
        try:
            self._worker.succeeded.disconnect()
            self._worker.failed.disconnect()
        except (RuntimeError, TypeError):  # nosec B110
            pass
        try:
            self._worker.cancel()
        except Exception:  # nosec B110
            pass
        self._worker = None

    def _on_loaded(self, data: dict):
        self._loading_label.setVisible(False)
        self._error_widget.setVisible(False)

        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._content_layout.addWidget(self._build_account_card(data))

        sub = self._find_subscription(data)
        if sub:
            self._content_layout.addWidget(self._build_subscription_card(sub))

        self._content_layout.addWidget(self._build_preferences_card())

        # Discreet footer: thin top separator, small muted Terms / Privacy links.
        footer = QFrame()
        footer.setObjectName("legalFooter")
        footer.setStyleSheet(
            "QFrame#legalFooter { border: none;"
            " border-top: 1px solid rgba(127,127,127,0.18); }"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 8, 0, 0)
        footer_layout.setSpacing(0)
        footer_layout.addStretch()
        legal_label = QLabel(
            f'<a href="{get_terms_url()}" style="color: rgba(128,128,128,0.85);'
            f' text-decoration: none;">{tr("Terms")}</a>'
            f' <span style="color: rgba(128,128,128,0.5);">·</span> '
            f'<a href="{get_privacy_url()}" style="color: rgba(128,128,128,0.85);'
            f' text-decoration: none;">{tr("Privacy")}</a>'
        )
        legal_label.setOpenExternalLinks(True)
        legal_label.setStyleSheet("font-size: 10px;")
        footer_layout.addWidget(legal_label)
        footer_layout.addStretch()
        self._content_layout.addWidget(footer)

        self._content_widget.setVisible(True)
        self.adjustSize()

    def _build_preferences_card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(_CARD_STYLE)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = QLabel(f"<b>{tr('Preferences')}</b>")
        title.setStyleSheet("font-size: 13px; color: palette(text);")
        layout.addWidget(title)
        layout.addWidget(self._build_output_folder_row())
        layout.addWidget(self._build_guidance_row())
        return card

    def _build_output_folder_row(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)

        label = QLabel(tr("AI Edit output folder"))
        label.setFixedWidth(_PREF_LABEL_W)
        label.setStyleSheet(_PREF_LABEL_STYLE)
        row.addWidget(label)

        self._output_dir_edit = QLineEdit()
        from qgis.core import QgsSettings
        current = QgsSettings().value("AIEdit/output_dir", "", type=str)
        self._output_dir_edit.setText(current)
        self._output_dir_edit.setPlaceholderText(tr("Default (auto)"))
        self._output_dir_edit.setToolTip(
            tr(
                "Where AI Edit writes its generated GeoTIFFs. "
                "Leave empty to use ~/Documents/AI Edit/ (or the saved project folder)."
            )
        )
        self._output_dir_edit.editingFinished.connect(self._on_output_dir_edited)
        self._output_dir_edit.setFixedHeight(24)
        row.addWidget(self._output_dir_edit, 1)

        browse_btn = QPushButton(tr("Browse..."))
        browse_btn.setFixedHeight(24)
        browse_btn.setStyleSheet(_PREF_BTN)
        browse_btn.setCursor(QtC.PointingHandCursor)
        browse_btn.clicked.connect(self._on_output_dir_browse)
        row.addWidget(browse_btn)

        return w

    def _build_guidance_row(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)

        label = QLabel(tr("Guidance tips"))
        label.setFixedWidth(_PREF_LABEL_W)
        label.setStyleSheet(_PREF_LABEL_STYLE)
        row.addWidget(label)
        row.addStretch(1)

        self._guidance_btn = QPushButton(tr("Show again"))
        self._guidance_btn.setFixedHeight(24)
        self._guidance_btn.setStyleSheet(_PREF_BTN)
        self._guidance_btn.setCursor(QtC.PointingHandCursor)
        self._guidance_btn.setToolTip(
            tr("Bring back the in-app tips you have closed (library, drawing, etc.).")
        )
        self._guidance_btn.clicked.connect(self._on_reset_guidance)
        row.addWidget(self._guidance_btn)

        return w

    def _on_reset_guidance(self) -> None:
        reset_hints()
        self._guidance_btn.setText(tr("Restored") + " ✓")
        self._guidance_btn.setEnabled(False)

    def _on_output_dir_edited(self) -> None:
        set_output_dir(self._output_dir_edit.text().strip())

    def _on_output_dir_browse(self) -> None:
        current = self._output_dir_edit.text().strip() or get_output_dir()
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Choose output folder"), current
        )
        if chosen:
            self._output_dir_edit.setText(chosen)
            set_output_dir(chosen)

    def _on_failed(self, message: str):
        self._loading_label.setVisible(False)
        self._error_label.setText(message)
        self._error_widget.setVisible(True)
        self._content_widget.setVisible(False)

    @staticmethod
    def _find_subscription(data: dict) -> dict | None:
        for s in data.get("subscriptions", []):
            if s.get("product_id") == PRODUCT_ID:
                return s
        return None

    def _build_account_card(self, data: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(_CARD_STYLE)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        email = data.get("email", "-")

        # A real account "chip": round avatar (first letter) + email + status.
        chip = QFrame()
        chip.setStyleSheet(
            "QFrame { background: palette(base);"
            " border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; }"
            "QLabel { background: transparent; border: none; }"
        )
        chip_row = QHBoxLayout(chip)
        chip_row.setContentsMargins(12, 10, 12, 10)
        chip_row.setSpacing(11)

        avatar = QLabel(email[:1].upper() if email and email != "-" else "?")
        avatar.setFixedSize(38, 38)
        avatar.setAlignment(QtC.AlignCenter)
        avatar.setStyleSheet(
            f"background: {BRAND_GREEN}; color: #14210A; border-radius: 19px;"
            " font-size: 17px; font-weight: 700;"
        )
        chip_row.addWidget(avatar)

        id_col = QVBoxLayout()
        id_col.setSpacing(2)
        email_val = QLabel(email)
        email_val.setTextInteractionFlags(QtC.TextSelectableByMouse)
        email_val.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: palette(text);"
        )
        id_col.addWidget(email_val)
        status_lbl = QLabel("✓ " + tr("Connected"))
        status_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {BRAND_GREEN_TEXT};"
        )
        id_col.addWidget(status_lbl)
        chip_row.addLayout(id_col, 1)

        # Sign out is a quiet inline link on the right of the chip, not a heavy
        # full-width button below the email.
        sign_out_btn = QPushButton(tr("Sign out"))
        sign_out_btn.setStyleSheet(_SIGNOUT_LINK)
        sign_out_btn.setCursor(QtC.PointingHandCursor)
        sign_out_btn.clicked.connect(self._on_sign_out)
        chip_row.addWidget(sign_out_btn, 0, QtC.AlignVCenter)

        layout.addWidget(chip)
        return card

    def _open_dashboard(self):
        QDesktopServices.openUrl(QUrl(get_dashboard_url()))

    def _build_subscription_card(self, sub: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(_CARD_STYLE)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)

        title = QLabel(f"<b>{PRODUCT_NAME}</b>")
        title.setStyleSheet("font-size: 13px; color: palette(text);")
        card_layout.addWidget(title)

        plan = sub.get("plan", "free")
        status = sub.get("status", "active")

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setSpacing(4)
        grid.setColumnMinimumWidth(0, 70)

        grid.addWidget(self._field_label(tr("Plan")), 0, 0)
        plan_text = self._format_plan(plan, status)
        status_text, status_color = _STATUS_DISPLAY.get(
            status, (status.title(), BRAND_RED)
        )
        plan_status = QLabel(f"{plan_text} · <span style='color:{status_color};'>{status_text}</span>")
        plan_status.setStyleSheet("font-size: 12px; color: palette(text);")
        grid.addWidget(plan_status, 0, 1)
        row = 1

        # Credits
        used = sub.get("usage_this_month", 0)
        limit = sub.get("quota_limit", 0)
        remaining = max(0, limit - used)
        is_free = plan == "free" and status != "trialing"

        grid.addWidget(self._field_label(tr("Credits")), row, 0)
        # Lime fill for the bar; the darker text tone for the number so it stays
        # AA-readable on the light dialog (the fill tone clears only ~2.5:1).
        credits_fill = BRAND_GREEN if remaining > 0 else BRAND_RED
        credits_text_color = BRAND_GREEN_TEXT if remaining > 0 else BRAND_RED
        if is_free:
            credits_text = f"{remaining} / {limit} {tr('free credits remaining')}"
        else:
            credits_text = f"{remaining} / {limit} {tr('credits remaining')}"
        credits_label = QLabel(credits_text)
        credits_label.setStyleSheet(f"font-size: 12px; color: {credits_text_color};")
        grid.addWidget(credits_label, row, 1)

        card_layout.addLayout(grid)

        progress = QProgressBar()
        progress.setRange(0, max(limit, 1))
        progress.setValue(remaining)
        progress.setTextVisible(False)
        progress.setFixedHeight(6)
        progress.setStyleSheet(
            f"QProgressBar {{ background: rgba(128,128,128,0.15);"
            f" border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {credits_fill};"
            f" border-radius: 3px; }}"
        )
        card_layout.addWidget(progress)

        # A small reset note (paid renewal date) above the action buttons.
        period_end = sub.get("current_period_end", "")
        if period_end and not is_free:
            reset_row = QHBoxLayout()
            reset_row.setContentsMargins(0, 2, 0, 0)
            reset_label = QLabel(f"{tr('Resets')} {self._format_date(period_end)}")
            reset_label.setStyleSheet("font-size: 10px; color: palette(text);")
            reset_row.addWidget(reset_label)
            reset_row.addStretch()
            card_layout.addLayout(reset_row)

        card_layout.addSpacing(4)

        # Subscribing/upgrading lives on the website (terra-lab.ai); the plugin
        # only points there via Manage account. Keeps billing out of the plugin.
        manage_btn = QPushButton(tr("Manage account on terra-lab.ai") + "  ↗")
        manage_btn.setStyleSheet(_MANAGE_BTN)
        manage_btn.setCursor(QtC.PointingHandCursor)
        manage_btn.setMinimumHeight(36)
        manage_btn.clicked.connect(self._open_dashboard)
        card_layout.addWidget(manage_btn)

        return card

    # -- Helpers --

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 11px; color: palette(text);")
        return label

    @staticmethod
    def _format_plan(plan: str, status: str) -> str:
        if plan == "pro":
            return "Pro"
        if status == "trialing":
            return tr("Free Trial")
        return tr("Free")

    @staticmethod
    def _format_date(iso_str: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%B %d, %Y")
        except (ValueError, AttributeError):
            return iso_str

    def _on_sign_out(self):
        from qgis.PyQt.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Sign out"))
        box.setText(tr("Sign out of AI Edit?"))
        box.setInformativeText(tr("You can sign back in anytime from QGIS."))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self.sign_out_requested.emit()
        self.accept()

    def done(self, result):  # noqa: N802 - Qt signature
        # accept()/reject() (Sign out, OK) dismiss the dialog without a
        # closeEvent, so cancel the in-flight loader here too rather than let it
        # complete with now-stale auth into a dismissed dialog.
        self._cancel_worker()
        super().done(result)

    def closeEvent(self, event):
        # The loader is a QgsTask now: no thread to wait on or terminate.
        # Disconnect + cancel so a late result can't fire into the closing
        # dialog; the task manager drains run() on its own.
        self._cancel_worker()
        super().closeEvent(event)

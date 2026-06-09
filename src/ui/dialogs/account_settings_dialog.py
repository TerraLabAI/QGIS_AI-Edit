"""Account Settings dialog for AI Edit plugin."""
from __future__ import annotations

from datetime import datetime

from qgis.PyQt.QtCore import QThread, pyqtSignal
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
    get_subscribe_url,
    get_terms_url,
)
from ...core.i18n import tr
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

_CARD_STYLE = (
    "QFrame { background: rgba(128,128,128,0.08);"
    " border: 1px solid rgba(128,128,128,0.2);"
    " border-radius: 6px; }"
    "QLabel { background: transparent; border: none; }"
    "QPushButton { background: transparent; }"
)

# Quiet local-preference rows (output folder, guidance) sit below the account
# cards. Aligned labels keep their controls on a shared left edge.
_PREF_LABEL_W = 92
_PREF_LABEL_STYLE = (
    "font-size: 11px; color: palette(text);"
    " background: transparent; border: none;"
)


class _AccountLoaderWorker(QThread):

    loaded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client, auth):
        super().__init__()
        self._client = client
        self._auth = auth

    def run(self):
        result = self._client.get_account(auth=self._auth)
        if "error" in result:
            self.failed.emit(result.get("error", "Unknown error"))
        else:
            self.loaded.emit(result)


class AccountSettingsDialog(QDialog):

    change_key_requested = pyqtSignal()

    def __init__(self, client, auth, activation_key, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Account Settings"))
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)

        self._activation_key = activation_key
        self._key_visible = False
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
        self._error_change_key_btn = QPushButton(tr("Change activation key"))
        self._error_change_key_btn.setStyleSheet(_LINK_BTN)
        self._error_change_key_btn.setCursor(QtC.PointingHandCursor)
        self._error_change_key_btn.clicked.connect(self._on_change_key)
        retry_row = QHBoxLayout()
        retry_row.addStretch()
        retry_row.addWidget(self._retry_btn)
        retry_row.addStretch()
        error_layout.addLayout(retry_row)
        change_row = QHBoxLayout()
        change_row.addStretch()
        change_row.addWidget(self._error_change_key_btn)
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

        self._worker = _AccountLoaderWorker(self._client, self._auth)
        self._worker.loaded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

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

        # Hairline divider between the account cards and the quiet pref rows.
        pref_sep = QFrame()
        pref_sep.setObjectName("prefSep")
        pref_sep.setStyleSheet(
            "QFrame#prefSep { border: none;"
            " border-top: 1px solid rgba(127,127,127,0.18); }"
        )
        pref_sep.setFixedHeight(1)
        self._content_layout.addSpacing(2)
        self._content_layout.addWidget(pref_sep)
        self._content_layout.addWidget(self._build_output_folder_row())
        self._content_layout.addWidget(self._build_guidance_row())

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

    def _build_output_folder_row(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)

        label = QLabel(tr("Output folder"))
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
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        email_row = QHBoxLayout()
        email_row.setSpacing(4)
        email_lbl = QLabel(f"<b>{tr('Email')}</b>")
        email_lbl.setStyleSheet("font-size: 12px; color: palette(text);")
        email_row.addWidget(email_lbl)
        email_row.addStretch()
        email_val = QLabel(data.get("email", "-"))
        email_val.setTextInteractionFlags(QtC.TextSelectableByMouse)
        email_val.setStyleSheet("font-size: 12px; color: palette(text);")
        email_row.addWidget(email_val)
        layout.addLayout(email_row)

        sep = QFrame()
        sep.setFrameShape(QtC.FrameHLine)
        sep.setStyleSheet("color: rgba(128,128,128,0.2);")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        key_row = QHBoxLayout()
        key_row.setSpacing(4)
        key_lbl = QLabel(f"<b>{tr('Key')}</b>")
        key_lbl.setStyleSheet("font-size: 12px; color: palette(text);")
        key_row.addWidget(key_lbl)
        key_row.addStretch()
        self._key_label = QLabel(self._masked_key())
        self._key_label.setTextInteractionFlags(QtC.TextSelectableByMouse)
        self._key_label.setStyleSheet(
            "font-size: 11px; font-family: monospace; color: palette(text);"
        )
        key_row.addWidget(self._key_label)
        self._toggle_btn = QPushButton(tr("Show"))
        self._toggle_btn.setStyleSheet(_LINK_BTN)
        self._toggle_btn.setCursor(QtC.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_key_visibility)
        key_row.addWidget(self._toggle_btn)
        layout.addLayout(key_row)

        change_row = QHBoxLayout()
        change_row.addStretch()
        change_btn = QPushButton(tr("Change activation key"))
        change_btn.setStyleSheet(_LINK_BTN)
        change_btn.setCursor(QtC.PointingHandCursor)
        change_btn.clicked.connect(self._on_change_key)
        change_row.addWidget(change_btn)
        layout.addLayout(change_row)

        return card

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

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 2, 0, 0)
        footer_row.setSpacing(6)

        if is_free and remaining == 0:
            left_label = QLabel(
                f'<a href="{get_subscribe_url()}" style="color: {BRAND_BLUE};">'
                f'{tr("Subscribe")}</a>'
            )
            left_label.setOpenExternalLinks(True)
            left_label.setStyleSheet(f"font-size: 10px; color: {BRAND_RED};")
            footer_row.addWidget(left_label)
        else:
            period_end = sub.get("current_period_end", "")
            if period_end and not is_free:
                reset_text = self._format_date(period_end)
                reset_label = QLabel(f"{tr('Resets')} {reset_text}")
                reset_label.setStyleSheet("font-size: 10px; color: palette(text);")
                footer_row.addWidget(reset_label)

        footer_row.addStretch()

        manage_label = QLabel(
            f'<a href="{get_dashboard_url()}" style="color: {BRAND_BLUE};'
            f' text-decoration: none;">{tr("Manage on terra-lab.ai")} ↗</a>'
        )
        manage_label.setOpenExternalLinks(True)
        manage_label.setStyleSheet("font-size: 10px;")
        footer_row.addWidget(manage_label)

        card_layout.addLayout(footer_row)

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

    def _masked_key(self) -> str:
        key = self._activation_key
        if len(key) <= 8:
            return key[:3] + "****"
        return key[:6] + "****" + key[-4:]

    def _toggle_key_visibility(self):
        self._key_visible = not self._key_visible
        if self._key_visible:
            self._key_label.setText(self._activation_key)
            self._toggle_btn.setText(tr("Hide"))
        else:
            self._key_label.setText(self._masked_key())
            self._toggle_btn.setText(tr("Show"))

    def _on_change_key(self):
        self.change_key_requested.emit()
        self.accept()

    def closeEvent(self, event):
        # The account loader thread has no event loop, so quit() is a no-op.
        # Wait gives the in-flight network call (built-in 5s timeout) room to
        # return; terminate is the last-resort exit when the network stack
        # itself is wedged, otherwise QThread destruction would crash QGIS.
        if self._worker and self._worker.isRunning():
            if not self._worker.wait(6000):
                self._worker.terminate()
                self._worker.wait(1000)
        super().closeEvent(event)

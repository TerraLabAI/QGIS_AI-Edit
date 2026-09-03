"""Account Settings dialog for AI Edit plugin."""
from __future__ import annotations

import html
from datetime import datetime

from qgis.PyQt.QtCore import QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import (
    get_contact_call_url,
    get_dashboard_url,
    get_privacy_url,
    get_terms_url,
)
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.pro_ceiling import pro_ceiling_contact_email
from ...workers.generic_request_task import GenericRequestTask
from ..dock.pro_ceiling import copy_email_to_clipboard
from ..dock_widget import (
    _BTN_LABEL_WEIGHT,
    BRAND_BLUE,
    BRAND_BLUE_HOVER,
    BRAND_GREEN,
    BRAND_GREEN_TEXT,
    BRAND_RED,
)
from ..onboarding_hint import reset_hints
from ..raster_writer import get_output_dir, set_output_dir

# Breathing room between the dialog and the edge of the screen it opens on.
_SCREEN_MARGIN_PX = 40

PRODUCT_ID = "ai-edit"
PRODUCT_NAME = "AI Edit"

_STATUS_DISPLAY = {
    "active": (tr("Active"), BRAND_GREEN_TEXT),
    "trialing": (tr("Free trial"), "#f5a623"),
    "canceled": (tr("Cancelled"), BRAND_RED),
}


def _status_display(status) -> tuple[str, str]:
    """Label and colour for a subscription status.

    A status the plugin has never heard of used to be dropped into the rich
    text label as the server wrote it. It is now escaped, tidied into words,
    and servable (copy.billing.status.<status>), so a billing status added
    after this release reads as a sentence rather than a raw token."""
    key = str(status or "")
    shipped, color = _STATUS_DISPLAY.get(key, ("", BRAND_RED))
    if not shipped:
        shipped = html.escape(key.replace("_", " ").title(), quote=False)
    return get_export_copy(f"billing.status.{key}", shipped, escape=True), color


_LINK_BTN = (
    f"QPushButton {{ border: none; color: {BRAND_BLUE}; font-size: 11px;"
    f" text-decoration: underline; padding: 2px 4px; background: transparent;"
    f" {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ color: {BRAND_BLUE_HOVER}; }}"
)

# Manage account: a quiet blue link in the plan card header, symmetric with
# the Sign out link in the account chip. A second full-width blue button read
# as an equal of the primary action and pushed the card taller for nothing.
_MANAGE_LINK = (
    f"QPushButton {{ border: none; background: transparent; color: {BRAND_BLUE};"
    f" font-size: 11px; font-weight: 600; padding: 2px 4px; }}"
    f"QPushButton:hover {{ color: {BRAND_BLUE_HOVER};"
    f" text-decoration: underline; }}"
)

# The two ways to reach us on the contact card: blue outline, equal weight.
_CONTACT_BTN = (
    f"QPushButton {{ background: transparent; color: {BRAND_BLUE};"
    f" border: 1px solid {BRAND_BLUE}; border-radius: 5px;"
    f" padding: 4px 12px; font-size: 12px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ color: {BRAND_BLUE_HOVER};"
    f" border-color: {BRAND_BLUE_HOVER}; }}"
)

# Compact sign-out link (sits inside the account chip, not a full-width button).
_SIGNOUT_LINK = (
    "QPushButton { border: none; background: transparent; color: palette(text);"
    f" font-size: 11px; text-decoration: underline; padding: 2px 4px;"
    f" {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ color: {BRAND_RED}; }}"
)

# Normal buttons inside a card need an explicit background, else the card's
# "QPushButton { background: transparent }" rule breaks native rendering.
# Text uses palette(text), NOT palette(button-text): the QGIS dark theme sets
# ButtonText to black on a dark Button, giving unreadable black-on-dark.
_PREF_BTN = (
    "QPushButton { background: palette(button); color: palette(text);"
    " border: 1px solid rgba(128,128,128,0.45); border-radius: 5px;"
    f" padding: 3px 12px; {_BTN_LABEL_WEIGHT} }}"
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
        self.setMinimumWidth(600)
        self.setMaximumWidth(720)
        # Fits a laptop screen: past this the content scrolls.
        self.setMaximumHeight(900)

        # The activation key now lives only in the web dashboard; Settings is
        # pure account management, so the key is intentionally not shown here.
        self._worker = None
        self._avatar = None
        self._avatar_worker = None

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
        # The cards outgrew a laptop screen, so they scroll inside the dialog
        # instead of pushing it past the bottom of the display.
        self._content_scroll = QScrollArea()
        self._content_scroll.setWidget(self._content_widget)
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QtC.FrameNoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        # Its size hint follows the cards, so adjustSize can open the dialog
        # tall enough to show them all instead of at the scroll area default.
        self._content_scroll.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents
        )
        self._content_scroll.setVisible(False)
        self._layout.addWidget(self._content_scroll, 1)

        self._client = client
        self._auth = auth
        self._fetch_account()

    def _fetch_account(self):
        self._loading_label.setVisible(True)
        self._error_widget.setVisible(False)
        self._content_scroll.setVisible(False)

        from qgis.core import QgsApplication

        # Drop any previous in-flight load (Retry) so its result can't land late.
        self._cancel_worker()

        auth = self._auth
        client = self._client
        self._worker = GenericRequestTask(
            "AI Edit account load",
            lambda: client.get_account(auth=auth),
            silent=True,
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

    def _start_avatar_load(self, url: str) -> None:
        """Download the profile photo off-thread and swap it into the avatar."""
        from qgis.core import QgsApplication
        client = self._client
        self._avatar_worker = GenericRequestTask(
            "AI Edit avatar load",
            lambda: client.download_image(url),
            silent=True,
        )
        self._avatar_worker.succeeded.connect(self._on_avatar_loaded)
        # Any failure (no network, not an image) keeps the letter badge.
        self._avatar_worker.failed.connect(lambda *_: None)
        QgsApplication.taskManager().addTask(self._avatar_worker)

    def _on_avatar_loaded(self, blob: bytes) -> None:
        if not blob:
            return
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtGui import QPainter, QPainterPath, QPixmap
        pm = QPixmap()
        if not pm.loadFromData(blob):
            return
        size = 38
        scaled = pm.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - size) // 2)
        y = max(0, (scaled.height() - size) // 2)
        scaled = scaled.copy(x, y, size, size)
        rounded = QPixmap(size, size)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        # The dialog may have closed before the download landed (guard the C++ obj).
        try:
            self._avatar.setText("")
            self._avatar.setPixmap(rounded)
            self._avatar.setStyleSheet("border-radius: 19px;")
        except (RuntimeError, AttributeError):  # nosec B110
            pass

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
            is_free = (
                sub.get("plan", "free") == "free"
                and sub.get("status", "active") != "trialing"
            )
            self._content_layout.addWidget(self._build_contact_card(is_free))

        # Preferences and Privacy are secondary, same-weight info: side by side
        # they read as one quiet row instead of extending the tower of
        # full-width cards, and the dialog stops needing a scrollbar. Wrapped in
        # a QWidget, not a bare layout, so the clear loop above removes it.
        two_up_row = QWidget()
        two_up = QHBoxLayout(two_up_row)
        two_up.setContentsMargins(0, 0, 0, 0)
        two_up.setSpacing(10)
        two_up.addWidget(self._build_preferences_card(), 1)
        two_up.addWidget(self._build_privacy_card(), 1)
        self._content_layout.addWidget(two_up_row)

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
        # Cards keep their own height; the surplus falls below them instead of
        # being shared out, which is what let a wrapped line overlap its row.
        self._content_layout.addStretch()

        self._content_scroll.setVisible(True)
        self._content_layout.activate()
        self._fit_to_content()
        # Once more after the dialog is on screen: a label only knows its true
        # wrapped height at the real width, which it does not have yet here.
        QtC.safe_single_shot(0, self, self._fit_to_content)

    def _fit_to_content(self) -> None:
        """Open tall enough to show every card, so no scrollbar is needed.

        The scroll area stays as the fallback for a short screen: the height is
        clamped to the work area of the screen this window is on, minus the
        frame, and the cards scroll only past that.
        """
        self.adjustSize()
        wanted = self._content_widget.sizeHint().height()
        chrome = max(0, self.height() - self._content_scroll.height())
        cap = self.maximumHeight()
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                frame_extra = max(0, self.frameGeometry().height() - self.height())
                cap = min(cap, screen.availableGeometry().height()
                          - frame_extra - _SCREEN_MARGIN_PX)
        except (AttributeError, RuntimeError):
            pass  # nosec B110 -- sizing is best-effort
        self.resize(self.width(), max(self.minimumSizeHint().height(),
                                      min(wanted + chrome, cap)))
        # A second pass: a wrapped label only knows its true height once it has
        # been laid out at the final width, so the first pass can still leave a
        # short scroll. Whatever is left over is added back, capped the same way.
        for _ in range(2):
            self._content_layout.activate()
            leftover = self._content_scroll.verticalScrollBar().maximum()
            if leftover <= 0 or self.height() >= cap:
                break
            self.resize(self.width(), min(self.height() + leftover, cap))

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
        # Label on its own line, then a full-width field + Browse below. Inline,
        # the field got squeezed to ~100px between the label and the button, so
        # a long path showed only its tail and read as cropped.
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(2, 0, 2, 0)
        col.setSpacing(5)

        label = QLabel(tr("AI Edit output folder"))
        label.setStyleSheet(_PREF_LABEL_STYLE)
        col.addWidget(label)

        field_row = QHBoxLayout()
        field_row.setContentsMargins(0, 0, 0, 0)
        field_row.setSpacing(8)

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
        self._output_dir_edit.setMinimumHeight(28)
        # Show the start of a long path, not its tail.
        self._output_dir_edit.setCursorPosition(0)
        field_row.addWidget(self._output_dir_edit, 1)

        browse_btn = QPushButton(tr("Browse..."))
        browse_btn.setMinimumHeight(28)
        browse_btn.setStyleSheet(_PREF_BTN)
        browse_btn.setCursor(QtC.PointingHandCursor)
        browse_btn.clicked.connect(self._on_output_dir_browse)
        field_row.addWidget(browse_btn)

        col.addLayout(field_row)
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

    def _build_privacy_card(self) -> QFrame:
        """Usage telemetry with an ON-by-default opt-out, mirroring AI
        Segmentation's Privacy card.

        Flips the shared TerraLab/telemetry_enabled flag (core/telemetry.py), so
        turning it off here also silences the sibling AI Segmentation plugin.

        It exists because the published privacy policy had to tell AI Edit users
        to email us to object, the plugin carrying no control of its own.
        """
        from ...core.telemetry import is_telemetry_enabled

        card = QFrame()
        card.setStyleSheet(_CARD_STYLE)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title = QLabel(f"<b>{tr('Privacy')}</b>")
        title.setStyleSheet("font-size: 13px; color: palette(text);")
        layout.addWidget(title)

        self._telemetry_checkbox = QCheckBox(
            tr("Share usage statistics with TerraLab"))
        self._telemetry_checkbox.setToolTip(tr("Helps us fix bugs faster."))
        self._telemetry_checkbox.setChecked(is_telemetry_enabled())
        self._telemetry_checkbox.setCursor(QtC.PointingHandCursor)
        self._telemetry_checkbox.setStyleSheet(
            "font-size: 12px; color: palette(text);")
        self._telemetry_checkbox.toggled.connect(self._on_telemetry_toggled)
        layout.addWidget(self._telemetry_checkbox)

        caption = QLabel(
            tr(
                "Errors, versions and which features you use, linked to your "
                "account. Never your imagery, layers or coordinates."
            )
        )
        caption.setWordWrap(True)
        caption.setStyleSheet("font-size: 11px; color: rgba(128,128,128,0.9);")
        layout.addWidget(caption)

        return card

    def _on_telemetry_toggled(self, enabled: bool) -> None:
        from ...core.telemetry import set_telemetry_enabled

        set_telemetry_enabled(enabled)

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
        self._content_scroll.setVisible(False)

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
        self._avatar = avatar
        chip_row.addWidget(avatar)
        # Swap the letter badge for the real profile photo when the account has
        # one (e.g. Google sign-in). Downloaded off-thread; the badge stays as a
        # fallback if there is no photo or the download fails.
        avatar_url = data.get("avatar_url")
        if avatar_url:
            self._start_avatar_load(avatar_url)

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

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel(f"<b>{PRODUCT_NAME}</b>")
        title.setStyleSheet("font-size: 13px; color: palette(text);")
        header.addWidget(title, 1)
        manage_btn = QPushButton(tr("Manage account") + " ↗")
        manage_btn.setStyleSheet(_MANAGE_LINK)
        manage_btn.setCursor(QtC.PointingHandCursor)
        manage_btn.setAutoDefault(False)
        manage_btn.setToolTip(
            tr("Opens your terra-lab.ai dashboard in the browser.")
        )
        manage_btn.clicked.connect(self._open_dashboard)
        header.addWidget(manage_btn, 0, QtC.AlignVCenter)
        card_layout.addLayout(header)

        plan = sub.get("plan", "free")
        status = sub.get("status", "active")

        plan_text = self._format_plan(plan, status)
        status_text, status_color = _status_display(status)
        plan_status = QLabel(
            f"{plan_text} · <span style='color:{status_color};'>{status_text}</span>"
        )
        plan_status.setStyleSheet("font-size: 12px; color: palette(text);")
        card_layout.addWidget(plan_status)

        # Credits
        used = sub.get("usage_this_month", 0)
        limit = sub.get("quota_limit", 0)
        remaining = max(0, limit - used)
        is_free = plan == "free" and status != "trialing"

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
        card_layout.addWidget(credits_label)

        # Free is for trying the plugin out, not for billable work. The pricing
        # page and the Terms of Use carry the same rule; a free user does their
        # work in here, so the line has to exist in here too.
        if is_free:
            free_use_note = QLabel(
                tr("Personal, non-commercial use. A paid plan adds commercial "
                   "use for one person."))
            free_use_note.setWordWrap(True)
            free_use_note.setStyleSheet("font-size: 11px; color: palette(mid);")
            card_layout.addWidget(free_use_note)

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

        # A small reset note above the action buttons: the subscription renewal
        # date for paid plans, monthly free-credit renewal (1st of the month)
        # for free.
        period_end = sub.get("current_period_end", "")
        if period_end:
            reset_row = QHBoxLayout()
            reset_row.setContentsMargins(0, 2, 0, 0)
            reset_label = QLabel(f"{tr('Resets')} {self._format_date(period_end)}")
            reset_label.setStyleSheet("font-size: 10px; color: palette(text);")
            reset_row.addWidget(reset_label)
            reset_row.addStretch()
            card_layout.addLayout(reset_row)

        return card

    def _build_contact_card(self, is_free: bool) -> QFrame:
        """The organisation contact, as one row under the plan card.

        It used to be a 10px grey line inside the plan card, below the Manage
        button, which read as a footnote to the billing link. A team buy has no
        self-serve path, so the ask is a card of its own at normal text size:
        the wording on the left, the two ways to reach us on the right, one row
        so it costs the dialog almost no height.
        """
        card = QFrame()
        card.setStyleSheet(_CARD_STYLE)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)

        heading = (
            tr("Working in a team or an organisation?") if is_free
            else tr("Need more than your plan?")
        )
        body = tr("Custom quota, team seats, invoices, or a custom AI solution.")

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        title = QLabel(f"<b>{html.escape(heading, quote=False)}</b>")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 12px; color: palette(text);")
        words.addWidget(title)
        line = QLabel(body)
        line.setWordWrap(True)
        line.setStyleSheet("font-size: 11px; color: palette(text);")
        words.addWidget(line)
        row.addLayout(words, 1)

        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        contact_email = pro_ceiling_contact_email()
        copy_btn = QPushButton(tr("Copy email"))
        copy_btn.setStyleSheet(_CONTACT_BTN)
        copy_btn.setCursor(QtC.PointingHandCursor)
        copy_btn.setMinimumHeight(28)
        copy_btn.setAutoDefault(False)
        copy_btn.clicked.connect(
            lambda: self._on_copy_contact_email(copy_btn, contact_email)
        )
        buttons.addWidget(copy_btn)

        # Booking a call is the only path that gets a real conversation, so it
        # sits beside the address. The URL is served (contact_call_url); when
        # the served value is unusable the shipped one stands, and only an
        # empty shipped constant hides the button.
        call_url = get_contact_call_url()
        if call_url:
            call_btn = QPushButton(tr("Book a call"))
            call_btn.setStyleSheet(_CONTACT_BTN)
            call_btn.setCursor(QtC.PointingHandCursor)
            call_btn.setMinimumHeight(28)
            call_btn.setAutoDefault(False)
            call_btn.clicked.connect(lambda: self._open_contact_call(call_url))
            buttons.addWidget(call_btn)
        row.addLayout(buttons, 0)

        return card

    @staticmethod
    def _on_copy_contact_email(button, address: str) -> None:
        """Put the address on the clipboard, and name the surface it came from."""
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "account_contact_copy"})
        copy_email_to_clipboard(button, address, idle_text=tr("Copy email"))

    @staticmethod
    def _open_contact_call(url: str) -> None:
        """Open the booking page, and name the surface the click came from."""
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "account_contact_call"})
        QDesktopServices.openUrl(QUrl(url))

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
            return tr("Free trial")
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

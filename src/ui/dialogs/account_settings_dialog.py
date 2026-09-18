"""The AI Edit settings dialog: AI Agent's left rail, one page at a time.

The rail opens on the banana mark and "AI Edit / by TerraLab", lists the pages
(Account, Billing, Tutorials, Keyboard shortcuts, More plugins) and two action
rows (Contact us, Report a problem) that open their flow and leave the page
that was showing selected. A free account gets "Do more with Pro" at the foot
of the rail (Upgrade to Pro), above the Terms and Privacy links.

This file keeps the frame: the rail, the stack, the account load, the avatar,
sign-out and teardown. The pages build in ``settings/``: ``account_page``,
``account_deletion``, ``billing_page``, ``help_pages``; the local preferences
(statistics, tips, output folder) in ``account_settings_preferences``.

The plugin opens it with ``AccountSettingsDialog(client, auth, activation_key,
parent)`` and listens to ``sign_out_requested``, ``account_deleted`` and
``usage_loaded``.
"""
from __future__ import annotations

import html

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import (
    build_utm_url,
    get_privacy_url,
    get_server_url,
    get_subscribe_url,
    get_terms_url,
)
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...workers.generic_request_task import GenericRequestTask
from ..dock.design_tokens import (
    BTN_PRIMARY_QSS,
    FONT_HINT,
    GREEN_TEXT,
    INK,
    INK_2,
    ORANGE_TEXT,
    RED_TEXT,
    category_ink,
    qcolor,
)
from ..external_url import open_external
from ..keyboard_focus import settle_dialog_default_button
from .account_settings_preferences import AccountPreferencesMixin
from .settings.account_deletion import AccountDeletionMixin
from .settings.account_page import AVATAR_PX, AccountPageMixin
from .settings.billing_page import BillingPageMixin
from .settings.category_tile import NAV_GLYPH_CATEGORIES
from .settings.help_pages import HelpPagesMixin
from .settings.plan_state import find_product_subscription, resolve_account_plan
from .settings.widgets import nav_qss, sidebar_qss

# What the backend routes and bills on: never rename the literal.
PRODUCT_ID = "ai-edit"
PRODUCT_NAME = "AI Edit"

# Word colours are the theme's text tokens (4.5:1 on both QGIS themes), never
# the brand leaf or a fill shade.
_STATUS_DISPLAY = {
    "active": (tr("Active"), GREEN_TEXT),
    "trialing": (tr("Free trial"), ORANGE_TEXT),
    "canceled": (tr("Cancelled"), RED_TEXT),
}


def _status_display(status, dark: bool = False) -> tuple[str, str]:
    """Label and colour for a subscription status.

    A status the plugin has never heard of is escaped, tidied into words, and
    servable (copy.billing.status.<status>), so a billing status added after
    this release reads as a sentence rather than a raw token. ``dark`` is
    kept for callers: the tokens already follow the theme."""
    key = str(status or "")
    shipped, color = _STATUS_DISPLAY.get(key, ("", RED_TEXT))
    if not shipped:
        shipped = html.escape(key.replace("_", " ").title(), quote=False)
    return get_export_copy(f"billing.status.{key}", shipped, escape=True), color


# The window: AI Agent's proportions for five pages. It opens at this size,
# clamped to the screen it lands on.
_DIALOG_W, _DIALOG_H = 900, 640
_DIALOG_MIN_W, _DIALOG_MIN_H = 720, 500
_SCREEN_MARGIN_PX = 40
_NAV_W = 196
_NAV_ROW_PX = 32
_WORDMARK_PX = 22
# The item role that carries a rail row's hue (``NAV_GLYPH_CATEGORIES``).
_NAV_CATEGORY_ROLE = Qt.ItemDataRole.UserRole + 1
_WORDMARK_QSS = f"font-size: 13px; font-weight: 700; color: {INK}; background: transparent;"
_WORDMARK_NOTE_QSS = f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent;"
_FOOT_LINK_STYLE = f"color: {INK_2}; text-decoration: none;"
_PRODUCT_PAGE_URL = build_utm_url("/ai-edit", "settings_wordmark")


class _WordmarkTile(QWidget):
    """The rail's mark and name as one clickable row."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(QtC.event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _StaticAuthHeader:
    """The auth header the dialog was opened with, shaped like the auth
    manager ``pro_page_link.open_pro_page`` asks for."""

    def __init__(self, header: dict):
        self._header = dict(header or {})

    def get_auth_header(self) -> dict:
        return dict(self._header)


class AccountSettingsDialog(
    AccountPageMixin,
    AccountDeletionMixin,
    BillingPageMixin,
    HelpPagesMixin,
    AccountPreferencesMixin,
    QDialog,
):

    sign_out_requested = pyqtSignal()
    # The account was accepted for erasure server-side. The plugin treats it
    # like a sign-out that nobody comes back from.
    account_deleted = pyqtSignal()
    # The /usage payload bundled into the account response (include=usage),
    # emitted only when the server sent it, so the dock refreshes its credits
    # without a second request.
    usage_loaded = pyqtSignal(dict)
    # Signed out: the Sign in button of the Account page. The plugin opens the
    # panel on its sign-in screen.
    sign_in_requested = pyqtSignal()

    def __init__(self, client, auth, activation_key, parent=None, signed_in: bool = True):
        super().__init__(parent)
        self.setWindowTitle(
            get_export_copy("dialogs.account_settings_dialog.settings_window_title", tr("AI Edit Settings"))
        )
        self.setModal(True)
        # The activation key lives only in the web dashboard; it is not shown.
        self._client = client
        self._auth = auth
        self._worker = None
        self._avatar = None
        self._avatar_worker = None
        self._email = ""
        self._delete_worker = None
        self._delete_btn = None
        # The refusal body, kept whole: GenericRequestTask.failed carries only
        # a sentence and a code, and ALREADY_SCHEDULED has a date to name.
        self._delete_detail: dict = {}
        self._siblings_widget = None
        self._page_row = 0
        # Signed out, the window still opens: Tutorials, Keyboard shortcuts,
        # Contact us and Report a problem are exactly what someone who cannot
        # sign in needs, and they were out of reach behind a greyed menu row.
        self._signed_in = bool(signed_in)
        self._fit_to_screen()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        self._pages = QStackedWidget(self)
        root.addWidget(self._pages, 1)

        self._add_page("person", tr("Account"), self._build_account_page())
        self._billing_row = self._nav.count()
        self._add_page("gem", tr("Billing"), self._build_billing_page())
        self._add_page("play", tr("Tutorials"), self._build_tutorials_page())
        self._add_page("terminal", tr("Keyboard shortcuts"), self._build_shortcuts_page())
        self._siblings_row = self._nav.count()
        self._add_page("puzzle", tr("More plugins"), self._build_siblings_page())
        # Rows that open a flow rather than a page. They sit after every page
        # row, so the page rows keep their index into the stack.
        self._add_action("chat_bubble", get_export_copy(
            "dock.tools_footer.contact_us_title", tr("Contact us")), "contact")
        self._add_action("warning", get_export_copy(
            "dialogs.error_report_dialog.title", tr("Report a problem")), "report")
        self._nav.setCurrentRow(0)
        # Several pages, no single filled action: Enter presses nothing, and
        # buttons take focus from Tab only.
        settle_dialog_default_button(self)
        if self._signed_in:
            self._fetch_account()
        else:
            self._paint_signed_out()

    # -- frame ---------------------------------------------------------------

    def _fit_to_screen(self) -> None:
        width, height = _DIALOG_W, _DIALOG_H
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                width = min(width, area.width() - _SCREEN_MARGIN_PX)
                height = min(height, area.height() - _SCREEN_MARGIN_PX)
        except (AttributeError, RuntimeError):
            pass  # nosec B110 -- sizing is best-effort
        self.setMinimumSize(min(_DIALOG_MIN_W, width), min(_DIALOG_MIN_H, height))
        self.resize(width, height)

    def _build_sidebar(self) -> QFrame:
        side = QFrame(self)
        side.setObjectName("settingsSidebar")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setStyleSheet(sidebar_qss("settingsSidebar"))
        side.setFixedWidth(_NAV_W)
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 14, 0, 12)
        col.setSpacing(6)
        col.addWidget(self._build_wordmark(side))

        self._nav = QListWidget(side)
        self._nav.setObjectName("settingsNav")
        self._nav.setStyleSheet(nav_qss("settingsNav"))
        self._nav.setIconSize(QSize(16, 16))
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav.setUniformItemSizes(True)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        col.addWidget(self._nav, 1)

        # The upgrade, at the foot of the rail, for a free account only: the
        # one ask a person can reach from whichever page they are on. Hidden
        # until the account says it is free, so nothing moves on a subscriber.
        self._upgrade_holder = QWidget(side)
        holder_row = QHBoxLayout(self._upgrade_holder)
        holder_row.setContentsMargins(12, 4, 12, 8)
        self._upgrade_pill = QPushButton(tr("Upgrade to Pro"), self._upgrade_holder)
        self._upgrade_pill.setStyleSheet(BTN_PRIMARY_QSS)
        self._upgrade_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upgrade_pill.setAutoDefault(False)
        self._upgrade_pill.setToolTip(tr("See plans on terra-lab.ai"))
        self._upgrade_pill.clicked.connect(self._on_upgrade)
        holder_row.addWidget(self._upgrade_pill)
        self._upgrade_holder.setVisible(False)
        col.addWidget(self._upgrade_holder)

        legal = QLabel(
            f'<a href="{html.escape(get_terms_url())}" style="{_FOOT_LINK_STYLE}">'
            f'{html.escape(tr("Terms"))}</a>'
            f' <span style="color: {INK_2};">·</span> '
            f'<a href="{html.escape(get_privacy_url())}" style="{_FOOT_LINK_STYLE}">'
            f'{html.escape(tr("Privacy"))}</a>', side)
        legal.setOpenExternalLinks(True)
        legal.setStyleSheet(f"font-size: {FONT_HINT}px;")
        legal.setContentsMargins(16, 0, 0, 0)
        col.addWidget(legal)
        return side

    def _build_wordmark(self, side: QFrame) -> QWidget:
        """The banana mark and "AI Edit / by TerraLab", opening the product page."""
        from ..icons import logo_pixmap, logo_size

        host = _WordmarkTile(side)
        host.clicked.connect(lambda: open_external(get_server_url("product_url", _PRODUCT_PAGE_URL)))
        host.setCursor(Qt.CursorShape.PointingHandCursor)
        host.setToolTip(tr("Open the AI Edit page"))
        host.setAccessibleName(host.toolTip())
        row = QHBoxLayout(host)
        row.setContentsMargins(14, 0, 12, 8)
        row.setSpacing(8)
        mark = QLabel(host)
        pixmap = logo_pixmap(mark, _WORDMARK_PX)
        if not pixmap.isNull():
            mark.setFixedSize(logo_size(_WORDMARK_PX))
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setPixmap(pixmap)
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        title = QLabel(PRODUCT_NAME, host)
        title.setStyleSheet(_WORDMARK_QSS)
        names.addWidget(title)
        maker = QLabel(tr("by TerraLab"), host)
        maker.setStyleSheet(_WORDMARK_NOTE_QSS)
        names.addWidget(maker)
        row.addLayout(names, 1)
        return host

    def _nav_icon(self, glyph: str):
        from ..icons import icon_for

        category = NAV_GLYPH_CATEGORIES.get(glyph)
        return icon_for(self, glyph, 16, qcolor(category_ink(category) if category else INK))

    def _add_page(self, glyph: str, label: str, page: QWidget) -> None:
        item = QListWidgetItem(self._nav_icon(glyph), label)
        item.setSizeHint(QSize(0, _NAV_ROW_PX))
        # The rail is narrow: a longer language elides the row, the tooltip
        # keeps the whole label.
        item.setToolTip(label)
        item.setData(_NAV_CATEGORY_ROLE, NAV_GLYPH_CATEGORIES.get(glyph, ""))
        self._nav.addItem(item)
        self._pages.addWidget(page)

    def _add_action(self, glyph: str, label: str, kind: str) -> None:
        item = QListWidgetItem(self._nav_icon(glyph), label)
        item.setSizeHint(QSize(0, _NAV_ROW_PX))
        # The rail is narrow: a longer language elides the row, the tooltip
        # keeps the whole label.
        item.setToolTip(label)
        item.setData(Qt.ItemDataRole.UserRole, f"action:{kind}")
        self._nav.addItem(item)

    def _on_nav_changed(self, row: int) -> None:
        item = self._nav.item(row) if row >= 0 else None
        data = str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""
        if data.startswith("action:"):
            # The rail never highlights a row with nothing behind it: the page
            # that was showing gets the selection back, then the flow opens.
            QtC.safe_single_shot(0, self, self._restore_page_row)
            opener = self._open_contact if data == "action:contact" else self._open_report
            QtC.safe_single_shot(0, self, opener)
            return
        if 0 <= row < self._pages.count():
            self._tint_nav_selection(item)
            self._page_row = row
            self._pages.setCurrentIndex(row)
            if row == self._siblings_row:
                self._refresh_siblings()

    def _tint_nav_selection(self, item) -> None:
        """The selected rail row wears its own page's hue."""
        category = str(item.data(_NAV_CATEGORY_ROLE) or "") if item is not None else ""
        self._nav.setStyleSheet(nav_qss("settingsNav", category or None))

    def _restore_page_row(self) -> None:
        if self._nav.currentRow() != self._page_row:
            self._nav.setCurrentRow(self._page_row)

    def show_page(self, index: int) -> None:
        self._nav.setCurrentRow(max(0, min(int(index), self._pages.count() - 1)))

    # -- account load --------------------------------------------------------

    def _fetch_account(self):
        from qgis.core import QgsApplication

        self._show_account_notice("")
        self._paint_account_loading()
        self._paint_billing_message(tr("Loading..."))
        self._upgrade_holder.setVisible(False)
        # Drop any previous in-flight load (Retry) so its result can't land late.
        self._cancel_worker()

        auth = self._auth
        client = self._client
        self._worker = GenericRequestTask(
            "AI Edit account load",
            lambda: client.get_account(auth=auth, include_usage=True),
            silent=True,
        )
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        QgsApplication.taskManager().addTask(self._worker)

    def _cancel_worker(self):
        """Disconnect then cancel the loader task so a late result never fires
        into a closed dialog. Cancellation is cooperative: a thread is never
        killed mid network-call."""
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
        data = data if isinstance(data, dict) else {}
        self._email = str(data.get("email") or "")
        usage = data.get("usage")
        if isinstance(usage, dict) and "error" not in usage and "images_used" in usage:
            self.usage_loaded.emit(dict(usage))
        plan = resolve_account_plan(data, PRODUCT_ID)
        self._paint_account(data, plan)
        self._paint_billing(plan)
        self._upgrade_holder.setVisible(plan.is_free)
        if self._delete_btn is not None and self._delete_worker is None:
            self._delete_btn.setEnabled(bool(self._email))

    def _on_failed(self, message: str, code: str = "", *_rest):
        self._paint_account_error(message, code)
        self._paint_billing_message(tr("Plan not loaded"))

    @staticmethod
    def _find_subscription(data: dict) -> dict | None:
        return find_product_subscription(data, PRODUCT_ID)

    # -- avatar --------------------------------------------------------------

    def _cancel_avatar_worker(self) -> None:
        """Same teardown as _cancel_worker, for the profile photo download."""
        worker = self._avatar_worker
        self._avatar_worker = None
        if worker is None:
            return
        try:
            worker.succeeded.disconnect()
            worker.failed.disconnect()
        except (RuntimeError, TypeError):  # nosec B110
            pass
        try:
            worker.cancel()
        except Exception:  # nosec B110
            pass

    def _start_avatar_load(self, url: str) -> None:
        """Download the profile photo off-thread and swap it into the avatar.
        Any failure (no network, not an image) keeps the letter badge."""
        from qgis.core import QgsApplication

        self._cancel_avatar_worker()
        client = self._client
        self._avatar_worker = GenericRequestTask(
            "AI Edit avatar load",
            lambda: client.download_image(url),
            silent=True,
        )
        self._avatar_worker.succeeded.connect(self._on_avatar_loaded)
        self._avatar_worker.failed.connect(lambda *_: None)
        QgsApplication.taskManager().addTask(self._avatar_worker)

    def _on_avatar_loaded(self, blob: bytes) -> None:
        if not blob or self._avatar is None:
            return
        from qgis.PyQt.QtGui import QPainter, QPainterPath, QPixmap

        pm = QPixmap()
        if not pm.loadFromData(blob):
            return
        size = AVATAR_PX
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
        # The card may have been repainted before the download landed.
        try:
            self._avatar.setText("")
            self._avatar.setStyleSheet("background: transparent; border: none;")
            self._avatar.setPixmap(rounded)
        except (RuntimeError, AttributeError):  # nosec B110
            pass

    # -- actions -------------------------------------------------------------

    def _on_upgrade(self) -> None:
        """The AI Edit plans, signed in through the same door as the dock's
        Subscribe buttons (pro_page_link.open_pro_page)."""
        from ..pro_page_link import open_pro_page

        def report(checkout_link: str) -> None:
            telemetry.track(te.SUBSCRIBE_LINK_CLICKED,
                            {"source": "upgrade_cta", "checkout_link": checkout_link})
            # The user leaves QGIS for the browser right after: ship now.
            telemetry.flush()

        open_pro_page(
            "plugin_settings",
            get_server_url("upgrade_url", get_subscribe_url()),
            client=self._client,
            auth_manager=_StaticAuthHeader(self._auth),
            on_outcome=report,
        )

    def _on_sign_out(self):
        from .confirm_dialog import question

        if not question(
            self,
            get_export_copy("dialogs.account_settings_dialog.sign_out_confirm_text", tr("Sign out of AI Edit?")),
            get_export_copy(
                "dialogs.account_settings_dialog.sign_out_confirm_info",
                tr("You can sign back in anytime."),
            ),
            default_yes=False,
            yes_label=get_export_copy("dialogs.account_settings_dialog.sign_out_button", tr("Sign out")),
        ):
            return
        self.sign_out_requested.emit()
        self.accept()

    def done(self, result):  # noqa: N802 - Qt signature
        # accept()/reject() dismiss the dialog without a closeEvent, so the
        # in-flight tasks are detached here too.
        self._cancel_worker()
        self._drop_delete_worker()
        self._cancel_avatar_worker()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802 - Qt signature
        # The loaders are QgsTasks: disconnect + cancel so a late result can't
        # fire into the closing dialog; the task manager drains run().
        self._cancel_worker()
        self._drop_delete_worker()
        self._cancel_avatar_worker()
        super().closeEvent(event)

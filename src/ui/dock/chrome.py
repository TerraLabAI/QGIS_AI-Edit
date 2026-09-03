from __future__ import annotations

import os

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.i18n import tr
from .style import (
    _BTN_BLUE_AUTH,
    _BTN_GHOST,
    _BTN_GREEN_AUTH,
    _BTN_LABEL_WEIGHT,
    _BTN_PAIR_CANCEL,
    _BTN_PAIR_NEUTRAL,
    BRAND_BLUE,
    BTN_GREEN,
    BTN_GREEN_HOVER,
    DOCK_BRANDING_URL,
    _btn_start_qss,
)
from .widgets import _Spinner

# Per-version banner state. Each key holds ONE version string, so closing the
# notice for 1.7.4 hides 1.7.4 and nothing else: the next release shows again.
_UPDATE_DISMISSED_KEY = "AIEdit/update_notice/dismissed_version"
_UPDATE_ANNOUNCED_KEY = "AIEdit/update_notice/announced_version"
# The banner is the loudest thing on the dock while an update is pending, and
# it still has to belong to the panel: the dock's own card ground with a 2px
# accent border, the way the first-run hero and the tutorial card are built.
_UPDATE_CARD_STYLE = (
    "QWidget#updateCard { background-color: rgba(30, 136, 229, 0.10);"
    f" border: 2px solid {BRAND_BLUE}; border-radius: 10px; }}"
    "QLabel { background: transparent; border: none; }"
)
_UPDATE_TITLE_STYLE = (
    "font-size: 13px; font-weight: 700; color: palette(text);"
    " background: transparent;"
)
_UPDATE_NOTE_STYLE = (
    "font-size: 11px; color: rgba(160, 164, 170, 0.95); background: transparent;"
)
_UPDATE_LATER_STYLE = (
    "QPushButton { background: transparent; color: rgba(150, 154, 160, 0.95);"
    f" border: none; font-size: 11px; padding: 2px 6px; {_BTN_LABEL_WEIGHT} }}"
    "QPushButton:hover { color: palette(text); text-decoration: underline; }"
)


class DockChromeMixin:
    """Title bar, activation section build, footer responsiveness, update
    banner, warning widget, and shared-widget placement for AIEditDockWidget."""

    def _setup_title_bar(self):
        """Custom title bar matching AI Segmentation style with close button."""
        title_widget = QWidget()
        title_outer = QVBoxLayout(title_widget)
        title_outer.setContentsMargins(0, 0, 0, 0)
        title_outer.setSpacing(0)

        # Title row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(4, 0, 0, 0)
        title_row.setSpacing(0)

        title_label = QLabel(
            "AI Edit by "
            f'<a href="{DOCK_BRANDING_URL}" '
            f'style="color: {BRAND_BLUE}; text-decoration: none;">TerraLab</a>'
        )
        title_label.setOpenExternalLinks(True)
        title_row.addWidget(title_label)
        title_row.addStretch()

        icon_size = self.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)

        float_btn = QToolButton()
        float_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
        )
        float_btn.setToolTip(tr("Dock or undock this panel"))
        float_btn.setFixedSize(icon_size + 4, icon_size + 4)
        float_btn.setAutoRaise(True)
        float_btn.clicked.connect(lambda: self.setFloating(not self.isFloating()))
        title_row.addWidget(float_btn)

        close_btn = QToolButton()
        close_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        )
        close_btn.setToolTip(tr("Close this panel"))
        close_btn.setFixedSize(icon_size + 4, icon_size + 4)
        close_btn.setAutoRaise(True)
        close_btn.clicked.connect(self.close)
        title_row.addWidget(close_btn)

        title_outer.addLayout(title_row)

        # Separator line (like AI Segmentation)
        separator = QFrame()
        separator.setFrameShape(QtC.FrameHLine)
        separator.setFrameShadow(QtC.FrameSunken)
        title_outer.addWidget(separator)

        self.setTitleBarWidget(title_widget)

    def _build_activation_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # --- Light headline (the gray instruction box is intentionally gone:
        # the button + one reassurance line is all a new user needs) ---
        layout.addSpacing(6)
        self._setup_header = QLabel(tr("Edit your map with AI") + " 🍌")
        self._setup_header.setAlignment(QtC.AlignCenter)
        self._setup_header.setStyleSheet(
            "font-weight: 600; font-size: 14px; color: palette(text);"
        )
        layout.addWidget(self._setup_header)

        layout.addSpacing(14)

        # --- Primary: one tap to sign in (browser handoff, no copy-paste) ---
        self._connect_section = QWidget()
        connect_layout = QVBoxLayout(self._connect_section)
        connect_layout.setContentsMargins(0, 0, 0, 0)
        connect_layout.setSpacing(6)

        self._connect_btn = QPushButton(tr("Sign in / Sign up to start"))
        self._connect_btn.setToolTip(tr("Sign in via your browser to start using AI Edit"))
        self._connect_btn.setMinimumHeight(38)
        self._connect_btn.setCursor(QtC.PointingHandCursor)
        self._connect_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        connect_layout.addWidget(self._connect_btn)

        # Value proposition in words, pixel-matched to the AI Segmentation
        # sign-in screen's checkmark card (same neutral frame, same rows) so
        # the two docks read as one family. An illustrative image and a
        # standalone free-AI-Edits hint were tried here and rejected:
        # text only. Since the 2026-07 monthly free plan, the first bullet
        # states the allowance: the monthly renewal is the plan's pitch and
        # must be explicit. The number is SERVED, never written here, so the
        # first promise a new user reads cannot drift from the real grant.
        hint_card = QFrame()
        hint_card.setObjectName("signinHintCard")
        hint_card.setStyleSheet(
            "QFrame#signinHintCard {"
            " border: 1px solid rgba(128,128,128,0.35);"
            " border-radius: 6px;"
            " background-color: rgba(128,128,128,0.08); }")
        hint_card_layout = QVBoxLayout(hint_card)
        hint_card_layout.setContentsMargins(10, 8, 10, 8)
        hint_card_layout.setSpacing(5)
        from ...core.paywall_state import advertised_free_generations

        for line in (
            tr("Free plan, {n} AI edits every month. Signing up takes 15 "
               "seconds in your browser.").replace(
                   "{n}", str(advertised_free_generations())),
            tr("Then type what to change on your imagery, and get the result "
               "back as a georeferenced layer."),
        ):
            row = QHBoxLayout()
            row.setSpacing(7)
            check = QLabel("✓")
            check.setStyleSheet(
                "font-size: 11px; font-weight: 600; color: #43a047;"
                " border: none; background: transparent;")
            row.addWidget(check, 0, QtC.AlignTop)
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                "font-size: 11px; color: palette(text);"
                " border: none; background: transparent;")
            row.addWidget(lbl, 1)
            hint_card_layout.addLayout(row)
        connect_layout.addWidget(hint_card)

        layout.addWidget(self._connect_section)

        # --- Waiting state: shown while the browser handoff is in progress ---
        self._pairing_wait_section = QWidget()
        wait_layout = QVBoxLayout(self._pairing_wait_section)
        wait_layout.setContentsMargins(0, 4, 0, 0)
        wait_layout.setSpacing(12)

        # Spinner + static status text on one centered row (no jumping dots).
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addStretch(1)
        self._pairing_spinner = _Spinner(16)
        status_row.addWidget(self._pairing_spinner, 0, QtC.AlignVCenter)
        self._pairing_status = QLabel(tr("Waiting for you to sign in in your browser"))
        self._pairing_status.setWordWrap(True)
        self._pairing_status.setStyleSheet("font-size: 12px; color: palette(text);")
        status_row.addWidget(self._pairing_status, 0, QtC.AlignVCenter)
        status_row.addStretch(1)
        wait_layout.addLayout(status_row)

        # Two compact, filled buttons side by side.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._pairing_reopen_btn = QPushButton(tr("Open again"))
        self._pairing_reopen_btn.setToolTip(tr("Didn't open? Open the page again"))
        self._pairing_reopen_btn.setMinimumHeight(28)
        self._pairing_reopen_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_reopen_btn.setStyleSheet(_BTN_PAIR_NEUTRAL)
        self._pairing_reopen_btn.clicked.connect(self._on_pairing_reopen_clicked)
        btn_row.addWidget(self._pairing_reopen_btn)

        self._pairing_cancel_btn = QPushButton(tr("Cancel"))
        self._pairing_cancel_btn.setMinimumHeight(28)
        self._pairing_cancel_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_cancel_btn.setStyleSheet(_BTN_PAIR_CANCEL)
        self._pairing_cancel_btn.clicked.connect(self._on_pairing_cancel_clicked)
        btn_row.addWidget(self._pairing_cancel_btn)
        wait_layout.addLayout(btn_row)

        # Copy the connect link so the user can finish sign-in in a different
        # browser (e.g. their default has no Google session). Standard CLI
        # device-flow fallback ("open browser, or copy this link").
        self._pairing_copy_btn = QPushButton(tr("Link not opening? Copy link"))
        self._pairing_copy_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_copy_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " color: palette(text); font-size: 11px; padding: 2px;"
            f" text-decoration: underline; {_BTN_LABEL_WEIGHT} }}"
        )
        self._pairing_copy_btn.clicked.connect(self._on_pairing_copy_clicked)
        wait_layout.addWidget(self._pairing_copy_btn, 0, QtC.AlignCenter)

        self._pairing_wait_section.setVisible(False)
        self._pairing_active = False
        layout.addWidget(self._pairing_wait_section)

        # One timer rotates the spinner while waiting. Parented to the dock
        # (segfault-safe) and stopped the moment the wait section hides.
        self._pairing_anim_timer = QTimer(self)
        self._pairing_anim_timer.setInterval(80)
        self._pairing_anim_timer.timeout.connect(self._pairing_spinner.advance)
        self._pending_pairing_code = ""
        self._pairing_link = ""

        layout.addStretch(1)

        # Activation message (errors / success)
        self._activation_message = QLabel("")
        self._activation_message.setAlignment(QtC.AlignCenter)
        self._activation_message.setWordWrap(True)
        self._activation_message.setStyleSheet("font-size: 11px;")
        self._activation_message.setVisible(False)
        layout.addWidget(self._activation_message)

        return widget

    def _set_credits_wanted(self, wanted: bool) -> None:
        self._credits_wanted = wanted
        self._apply_footer_responsive()

    def _apply_footer_responsive(self) -> None:
        """Collapse low-priority footer items, by priority, until the row fits
        the dock width. Measured (not threshold-based) so it stays correct
        across font size, DPI and translated pill length.

        The right-side icons (vectorize / swipe / settings / help) are never
        touched - they always stay reachable. Kept longest -> dropped first:
          1. usage count label ("100 / 200")
          2. credit ring (last resort only)
        """
        scroll = getattr(self, "_scroll_area", None)
        if scroll is None:
            return
        # Viewport excludes the vertical scrollbar; subtract the main layout's
        # 8px left/right margins to get the width the footer row actually gets.
        avail = scroll.viewport().width() - 16
        if avail <= 0:
            return

        # Start from the fullest state the current data allows, then collapse.
        self._credits_label.setVisible(self._credits_wanted)
        self._credit_ring.setVisible(self._credits_wanted)

        def fits() -> bool:
            # invalidate() drops the layout's cached hint so the measurement
            # reflects the visibility / text changes made just above.
            self._footer_row.invalidate()
            return self._footer_row.sizeHint().width() <= avail

        if not fits() and self._credits_wanted:
            self._credits_label.setVisible(False)
        if not fits() and self._credits_wanted:
            self._credit_ring.setVisible(False)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_footer_responsive()

    def _setup_update_notification(self, parent_layout: QVBoxLayout) -> None:
        """Build the 'update available' banner, hidden until check_for_updates finds one.

        Pinned at the bottom of the dock (above the footer) as a sibling of the
        main sections, so it stays visible in every state (idle, generating,
        result) and even while ``_main_widget`` is hidden (unactivated state,
        tool panels). Most users never check for plugin updates, so this banner
        is how they learn a newer version exists.
        """
        # A card, full width, at the top of the dock: an update the user never
        # notices is an update they never install, and this is the one message
        # that has to win over whatever step they are on. Built like the
        # panel's other cards so it reads as part of the dock, not a banner
        # dropped on top of it.
        self._update_notif_container = QWidget()
        self._update_notif_container.setObjectName("updateCard")
        self._update_notif_container.setStyleSheet(_UPDATE_CARD_STYLE)
        card = QVBoxLayout(self._update_notif_container)
        card.setContentsMargins(12, 12, 12, 12)
        card.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        icon_label = QLabel()
        icon_label.setFixedSize(36, 36)
        icon_label.setScaledContents(True)
        # chrome.py sits at src/ui/dock/, so the plugin root is four levels up.
        plugin_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )))
        icon_path = os.path.join(plugin_root, "resources", "icons", "icon.png")
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap)
        head.addWidget(icon_label, 0, QtC.AlignTop)

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        self._update_title_label = QLabel("")
        self._update_title_label.setWordWrap(True)
        self._update_title_label.setStyleSheet(_UPDATE_TITLE_STYLE)
        words.addWidget(self._update_title_label)
        # One line, and only one: the served release note. It says what the
        # release brings, which is the whole reason to press the button.
        self._update_notification_label = QLabel("")
        self._update_notification_label.setWordWrap(True)
        self._update_notification_label.setStyleSheet(_UPDATE_NOTE_STYLE)
        words.addWidget(self._update_notification_label)
        head.addLayout(words, 1)
        card.addLayout(head)

        self._update_now_btn = QPushButton(tr("Update now"))
        self._update_now_btn.setCursor(QtC.PointingHandCursor)
        self._update_now_btn.setStyleSheet(_btn_start_qss(_BTN_BLUE_AUTH))
        self._update_now_btn.setMinimumHeight(38)
        self._update_now_btn.setAutoDefault(False)
        self._update_now_btn.clicked.connect(self._on_open_plugin_manager)
        card.addWidget(self._update_now_btn)

        self._offered_update_version = ""
        self._update_banner_armed = False
        later_row = QHBoxLayout()
        later_row.setContentsMargins(0, 0, 0, 0)
        later_row.addStretch()
        later_btn = QPushButton(tr("Later"))
        later_btn.setCursor(QtC.PointingHandCursor)
        later_btn.setStyleSheet(_UPDATE_LATER_STYLE)
        later_btn.setAutoDefault(False)
        later_btn.clicked.connect(self._on_dismiss_update_banner)
        later_row.addWidget(later_btn)
        later_row.addStretch()
        card.addLayout(later_row)

        self._update_notif_container.setVisible(False)
        parent_layout.addWidget(self._update_notif_container)

    def sync_update_banner(self) -> None:
        """Show the pending update, except while a generation is running.

        A run owns the panel and its progress; the banner would sit on top of
        it offering to swap the plugin out mid-flight.
        """
        container = getattr(self, "_update_notif_container", None)
        if container is None:
            return
        busy = False
        try:
            busy = bool(self._progress_widget.isVisible())
        except (RuntimeError, AttributeError):
            pass
        container.setVisible(bool(self._update_banner_armed) and not busy)

    def check_for_updates(self) -> bool:
        """Offer the update only when QGIS can actually install it.

        The banner used to fire on the served latest_version alone. QGIS
        usually has not fetched the plugin repository yet at that point, so
        Update now opened a Plugin Manager whose Upgradeable tab was empty and
        the user could do nothing for ten minutes. The rule is now the
        installer's own verdict: status == "upgradeable", nothing else.

        The served version is only the hint that a refresh is worth making.
        Returns True once the banner is up, so the caller can stop polling.
        """
        version = self._upgradeable_version()
        if version:
            self._show_update_banner(version, "plugin_registry")
            return True
        self._maybe_refresh_plugin_repository()
        return False

    def _show_update_banner(self, version: str, trigger: str) -> None:
        """Offer one version, with the served line about what it brings.
        Silent when the user already closed the notice for it."""
        if self._is_update_dismissed(version):
            return
        self._update_title_label.setText(
            tr("AI Edit {version} is out").format(version=version)
        )
        note = self._served_update_line()
        self._update_notification_label.setText(note)
        self._update_notification_label.setVisible(bool(note))
        self._offered_update_version = version
        self._update_banner_armed = True
        self.sync_update_banner()
        self._track_update_prompt_shown(version, trigger)

    def _plugin_installer_key(self) -> str:
        """The key pyplugin_installer files this install under: the folder
        name. A dev clone whose folder differs from the published id is never
        listed, which is why a dev install sees no banner."""
        return os.path.basename(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        )

    def _upgradeable_version(self) -> str:
        """The version QGIS says is installable, or "" when there is none."""
        try:
            from pyplugin_installer.installer_data import plugins

            data = plugins.all().get(self._plugin_installer_key())
            if data and data.get("status") == "upgradeable":
                return str(data.get("version_available") or "")
        except Exception:
            pass  # nosec B110  No repo metadata yet, dev install, etc.
        return ""

    def _maybe_refresh_plugin_repository(self) -> None:
        """One non-blocking repository refresh per session, when the server
        says a newer version exists and QGIS has not caught up.

        fetchAvailablePlugins() is not usable here: it opens a modal fetching
        dialog and exec()s it, which freezes QGIS. requestFetching() is the
        same work without the dialog, and checkingDone tells us when to look
        again.
        """
        if getattr(self, "_update_refresh_requested", False):
            return
        served = self._served_newer_version()
        if not served:
            return
        self._update_refresh_requested = True
        try:
            from pyplugin_installer.installer_data import repositories
        except Exception:
            self._track_update_prompt_suppressed(served, "installer_unavailable")
            return
        try:
            enabled = list(repositories.allEnabled())
            if not enabled:
                self._track_update_prompt_suppressed(served, "no_repository")
                return
            repositories.checkingDone.connect(self._on_plugin_repository_checked)
            for key in enabled:
                repositories.requestFetching(key, force_reload=True)
        except Exception:
            self._track_update_prompt_suppressed(served, "refresh_failed")

    def _on_plugin_repository_checked(self) -> None:
        """The repository answered: rebuild the plugin list and look again."""
        served = self._served_newer_version()
        try:
            from pyplugin_installer.installer_data import plugins, repositories

            try:
                repositories.checkingDone.disconnect(self._on_plugin_repository_checked)
            except (TypeError, RuntimeError):
                pass
            plugins.rebuild()
        except Exception:
            if served:
                self._track_update_prompt_suppressed(served, "refresh_failed")
            return
        version = self._upgradeable_version()
        if version:
            self._show_update_banner(version, "plugin_registry_refreshed")
        elif served:
            self._track_update_prompt_suppressed(served, "not_listed")

    def _served_newer_version(self) -> str:
        """The served version when it is ahead of the installed one, else "".

        Cache-only: the plugin makes no network call to answer this.
        """
        try:
            from ...core.auth.activation_manager import (
                is_update_available,
                served_latest_version,
            )

            if not self._installed_plugin_version():
                return ""
            latest = served_latest_version()
            if latest and is_update_available(self._installed_plugin_version()):
                return str(latest)
        except Exception:
            pass  # nosec B110  A bad nudge must never break the dock.
        return ""

    def _track_update_prompt_suppressed(self, served_version: str, reason: str) -> None:
        """Report an offer we chose not to make, once per reason per session."""
        seen = getattr(self, "_update_suppressed_reported", None)
        if seen is None:
            seen = set()
            self._update_suppressed_reported = seen
        if reason in seen:
            return
        seen.add(reason)
        try:
            from ...core import telemetry
            from ...core import telemetry_events as te

            telemetry.track(
                te.PLUGIN_UPDATE_PROMPT_SUPPRESSED,
                {"served_version": served_version, "reason": reason},
            )
        except Exception:
            pass  # nosec B110  Telemetry must never break the dock.

    @staticmethod
    def _served_update_line() -> str:
        """The one served sentence to put under the offer: the release note
        first, the generic update message when there is none."""
        try:
            from ...core.auth.activation_manager import (
                served_release_notes_line,
                served_update_message,
            )

            return served_release_notes_line() or served_update_message() or ""
        except Exception:
            return ""  # A bad line must never break the dock.

    @staticmethod
    def _is_update_dismissed(version: str) -> bool:
        try:
            from qgis.PyQt.QtCore import QSettings

            from ...core.auth.activation_manager import is_update_dismissed

            return is_update_dismissed(version, QSettings().value(_UPDATE_DISMISSED_KEY, "", type=str))
        except Exception:
            return False

    def _on_dismiss_update_banner(self) -> None:
        version = getattr(self, "_offered_update_version", "")
        self._update_banner_armed = False
        self._update_notif_container.setVisible(False)
        try:
            from qgis.PyQt.QtCore import QSettings

            QSettings().setValue(_UPDATE_DISMISSED_KEY, version)
        except Exception:
            pass  # nosec B110
        self._track_update_prompt_clicked(version, "dismissed")

    def _track_update_prompt_shown(self, version: str, trigger: str) -> None:
        """Once per version per install, not once per session."""
        try:
            from qgis.PyQt.QtCore import QSettings

            settings = QSettings()
            if str(settings.value(_UPDATE_ANNOUNCED_KEY, "", type=str)) == version:
                return
            settings.setValue(_UPDATE_ANNOUNCED_KEY, version)
        except Exception:
            pass  # nosec B110  Settings unreadable: still report it once.
        self._track_update_event(True, version, trigger)

    def _track_update_prompt_clicked(self, version: str, action: str) -> None:
        self._track_update_event(False, version, action)

    @staticmethod
    def _track_update_event(shown: bool, version: str, kind: str) -> None:
        try:
            from ...core import telemetry
            from ...core import telemetry_events as te

            if shown:
                telemetry.track(te.PLUGIN_UPDATE_PROMPT_SHOWN, {
                    "offered_version": version, "trigger": kind,
                })
            else:
                telemetry.track(te.PLUGIN_UPDATE_PROMPT_CLICKED, {
                    "offered_version": version, "action": kind,
                })
        except Exception:
            pass  # nosec B110  Telemetry never breaks the dock.

    @staticmethod
    def _installed_plugin_version() -> str:
        """Read version= from this plugin's metadata.txt ('' when unreadable)."""
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        try:
            with open(os.path.join(root, "metadata.txt"), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("version="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            pass
        return ""

    def _on_open_plugin_manager(self, _link: str = "") -> None:
        """Plugin Manager on the Upgradeable tab, the Manage Plugins action, or
        the marketplace page. Same three ways in as the TerraLab menu entry."""
        action = "plugin_manager"
        try:
            from ...core.auth.activation_manager import served_marketplace_url

            url = served_marketplace_url()
            if not self._upgradeable_version():
                # The Upgradeable tab would be empty, so the marketplace page
                # is the only place the click can still lead somewhere.
                from ..external_url import open_external

                open_external(url)
                action = "marketplace_page"
            else:
                from ..terralab_menu import open_plugin_manager_updates

                action = open_plugin_manager_updates(url)
        except Exception:
            pass  # nosec B110
        self._track_update_prompt_clicked(getattr(self, "_offered_update_version", ""), action)

    def _build_warning_widget(self) -> QWidget:
        """Build the empty-canvas first-run hero (shown when no visible layer).

        The empty state IS the onboarding. It leads with the truth the user
        must act on - the imagery is THEIRS to bring (any GeoTIFF / WMS / XYZ)
        - and keeps a one-click "Load a sample image" demo as the reassurance
        fallback for someone with no data on hand (Yvann 2026-07-08). The
        ghost secondary button adapts to the actual blocker (see
        _sync_warning_actions): Data Source Manager when the project holds no
        layers, one-click re-check of the topmost raster when layers exist
        but are all unchecked. It mirrors
        the AI Segmentation hero pixel for pixel so the two docks read as one
        family. No illustrative preview image: real product output only, a
        glyph is fine. The plugin handles the demo: it only adds a basemap
        and frames the scene; drawing a zone and writing a prompt stays the
        user's own step, same as with any imagery they bring in.

        Layout: a transparent, vertically-EXPANDING wrapper holds the compact
        blue-tinted card at the TOP with a single stretch below it, so when it
        is added with stretch factor 1 the card pins to the top and the surplus
        falls below. The plugin reads top-to-bottom, so the empty state starts
        at the top too (Yvann 2026-07-08), never centered."""
        wrapper = QWidget()
        wrapper.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QWidget()
        card.setObjectName("firstRunHero")
        card.setStyleSheet(
            "QWidget#firstRunHero { background-color: rgba(30, 136, 229, 0.08); "
            "border: 1px solid rgba(30, 136, 229, 0.28); border-radius: 6px; }"
            "QLabel { background: transparent; border: none; color: palette(text); }"
        )
        col = QVBoxLayout(card)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(7)

        glyph = QLabel("🗺️")
        glyph.setAlignment(QtC.AlignCenter)
        glyph.setStyleSheet("font-size: 26px;")
        col.addWidget(glyph)

        self._warning_title = QLabel(tr("Load your own imagery"))
        self._warning_title.setWordWrap(True)
        self._warning_title.setAlignment(QtC.AlignCenter)
        self._warning_title.setStyleSheet("font-weight: 700; font-size: 15px;")
        col.addWidget(self._warning_title)

        # One quiet line, one job: name what counts as imagery. No workflow
        # prose. Kept as _warning_text so show_basemap_error can swap it and
        # _sync_warning_actions can retune it per state.
        self._warning_text = QLabel(tr("Any GeoTIFF, WMS or XYZ basemap."))
        self._warning_text.setWordWrap(True)
        self._warning_text.setAlignment(QtC.AlignCenter)
        self._warning_text.setStyleSheet(
            "font-size: 11px; color: rgba(128, 128, 128, 0.95);")
        col.addWidget(self._warning_text)

        # The card's headline names the blocker, so the card owes the user the
        # door past it. Ghost, not filled: the example button below is the
        # screen's single filled primary and this one must recede. Text, tooltip
        # and click target are state-dependent (_sync_warning_actions).
        self._warning_show_layers_mode = False
        self._warning_error_text_active = False
        self._add_layer_btn = QPushButton(tr("Add a layer…"))
        self._add_layer_btn.setToolTip(
            tr("Open QGIS's Data Source Manager to add imagery")
        )
        self._add_layer_btn.setCursor(QtC.PointingHandCursor)
        self._add_layer_btn.setMinimumHeight(32)
        self._add_layer_btn.setStyleSheet(_BTN_GHOST)
        self._add_layer_btn.clicked.connect(self._on_add_layer_clicked)
        col.addSpacing(4)
        col.addWidget(self._add_layer_btn)

        # 'or' divider: the structural device that splits the two real paths
        # (bring your own vs. try a sample), so the example reads as the
        # fallback without a sentence spelling it out.
        def _rule():
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(
                "background-color: rgba(128, 128, 128, 0.28); border: none;")
            return line

        div = QHBoxLayout()
        div.setContentsMargins(0, 0, 0, 0)
        div.setSpacing(8)
        or_lbl = QLabel(tr("or"))
        or_lbl.setStyleSheet("font-size: 10px; color: rgba(128, 128, 128, 0.8);")
        div.addWidget(_rule(), 1)
        div.addWidget(or_lbl, 0)
        div.addWidget(_rule(), 1)
        col.addSpacing(2)
        col.addLayout(div)
        col.addSpacing(2)

        # Generous primary: the empty canvas is the funnel's cliff, and this
        # button is the one-click way off it, so it reads as an invitation
        # (tall, bold, arrow), not a fallback. The label names what the click
        # actually delivers - an image on the canvas - because "try an example"
        # left users guessing. Glyph outside tr().
        self._basemap_btn = QPushButton(tr("Load a sample image") + "  →")
        self._basemap_btn.setCursor(QtC.PointingHandCursor)
        self._basemap_btn.setMinimumHeight(38)
        self._basemap_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
            " font-size: 14px; font-weight: 700; padding: 8px 16px;"
            " border: none; border-radius: 6px; }"
            f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER};"
            " color: #000000; }"
        )
        self._basemap_btn.clicked.connect(self._on_try_example_clicked)
        col.addWidget(self._basemap_btn)

        outer.addWidget(card)
        outer.addStretch(1)
        return wrapper

    def _sync_warning_actions(self) -> None:
        """Point the hero's secondary button at the actual blocker. Two states,
        re-decided every time the hero (re)shows via _update_layer_warning:

        - Project holds NO layers: "Add a layer…" opens the Data Source
          Manager (the imagery really is missing).
        - Project holds layers but every one is unchecked: "Show my layers"
          re-checks the topmost raster. Dumping this user into the full import
          dialog when the data is already in the project was the complaint
          that created this split.
        """
        from qgis.core import QgsProject

        root = QgsProject.instance().layerTreeRoot()
        has_layers = any(
            node.layer() is not None for node in root.findLayers()
        )
        self._warning_show_layers_mode = has_layers
        if has_layers:
            self._warning_title.setText(tr("Your layers are hidden"))
            self._add_layer_btn.setText(tr("Show my layers"))
            self._add_layer_btn.setToolTip(
                tr("Re-check your topmost layer in the Layers panel")
            )
            # A stale basemap-load error belongs to the no-layer state; the
            # hidden-layers line replaces it.
            self._warning_error_text_active = False
            self._warning_text.setText(
                tr("Nothing shows on the map until one is checked.")
            )
        else:
            self._warning_title.setText(tr("Load your own imagery"))
            self._add_layer_btn.setText(tr("Add a layer…"))
            self._add_layer_btn.setToolTip(
                tr("Open QGIS's Data Source Manager to add imagery")
            )
            if not self._warning_error_text_active:
                self._warning_text.setText(
                    tr("Any GeoTIFF, WMS or XYZ basemap.")
                )

    def _reveal_topmost_layer(self) -> None:
        """Re-check the topmost raster in the layer tree (topmost layer of any
        kind when no raster exists), plus any unchecked ancestor group so the
        map actually shows pixels. The layerTreeRoot's visibilityChanged
        binding then re-runs _update_layer_warning and the hero retires on
        its own; nothing here touches the dock state directly."""
        from qgis.core import QgsProject, QgsRasterLayer

        root = QgsProject.instance().layerTreeRoot()
        nodes = [n for n in root.findLayers() if n.layer() is not None]
        if not nodes:
            return
        node = next(
            (n for n in nodes if isinstance(n.layer(), QgsRasterLayer)),
            nodes[0],
        )
        node.setItemVisibilityChecked(True)
        parent = node.parent()
        while parent is not None and parent is not root:
            parent.setItemVisibilityChecked(True)
            parent = parent.parent()

    def _on_add_layer_clicked(self) -> None:
        """The hero's secondary button: reveal the topmost hidden layer when
        the project already has layers (see _sync_warning_actions), else open
        QGIS's own Data Source Manager. QgisInterface exposes no action for
        that dialog, and its ``openDataSourceManagerPage`` only landed in QGIS
        3.30, below which this plugin still runs; the main window's
        ``mActionDataSourceManager`` has carried it since QGIS 3.0, so it goes
        first and the newer call is the fallback. When neither exists the
        click does nothing: the example button below stays the way out."""
        if self._warning_show_layers_mode:
            self._reveal_topmost_layer()
            return
        try:
            from qgis.utils import iface

            action = iface.mainWindow().findChild(
                QtC.QAction, "mActionDataSourceManager"
            )
            if action is not None:
                action.trigger()
                return
            iface.openDataSourceManagerPage(None)
        except Exception:
            pass  # nosec B110  An old or headless QGIS must not raise here.

    def _on_try_example_clicked(self):
        """One-click unblock for the empty-canvas gate. The heavy lifting (add
        a basemap, frame the demo scene) lives in the plugin, which owns the
        canvas; the dock only asks for it and stays a pure state machine. The
        user still draws their own zone and prompt from there.

        Behind the server kill switch: the demo pulls tiles from an outside
        source, so it has to be switchable off without a release when that
        source is down."""
        if self._feature_blocked("demo"):
            return
        self.try_example_requested.emit()

    def _sync_demo_button(self) -> None:
        """Hide the example button when the demo is switched off server-side,
        so the first-run card offers only what actually works."""
        from ...core.auth.activation_manager import is_feature_enabled

        if getattr(self, "_basemap_btn", None) is not None:
            self._basemap_btn.setVisible(is_feature_enabled("demo"))

    def show_basemap_error(self):
        """Surface a load failure in the warning box (called by the plugin when
        neither the demo nor the fallback basemap could be added). The flag
        keeps _sync_warning_actions from overwriting it while the no-layer
        state lasts."""
        self._warning_error_text_active = True
        self._warning_text.setText(tr(
            "Couldn't load the example basemap. Check your internet "
            "connection, or add your own layer (GeoTIFF, WMS, XYZ)."
        ))

    def _place_reference_widget(self, target: str) -> None:
        """Inject the shared refs strip into the active prompt container.

        ``target`` is "prompt" or "result". The strip lives above the textbox
        inside the bordered container, so the whole input area reads as a
        single ChatGPT-style attachment block.
        """
        if self._reference_widget is None:
            return
        container = (
            self._prompt_container if target == "prompt" else self._result_prompt_container
        )
        container.insert_refs_widget(self._reference_widget)
        # Visibility tracks the store: hidden when 0 refs, shown when ≥1.
        self._reference_widget.setVisible(self._reference_widget.count() > 0)
        self._reference_widget.setEnabled(True)
        # `set_generating(True)` flips this flag on every run, but the
        # generation-done path (set_generation_complete / set_initial_state)
        # never calls set_generating(False), so without this reset the flag
        # stays True and silently blocks +/paste/drop on every subsequent
        # attempt.
        self._reference_widget.set_readonly(False)
        # The Reference panel is a second view over the same store and is
        # locked in step with the strip, so it has to be released in step too.
        # It was only released in set_generating(False), which that same
        # generation-done path never reaches: both its import buttons stayed
        # greyed from the first successful generation to the end of the
        # session, with no way back short of restarting QGIS.
        panel = getattr(self, "_reference_panel", None)
        if panel is not None:
            panel.set_readonly(False)
        self._sync_attach_buttons()

    def _place_version_strip(self, target: str) -> None:
        """Re-home the version strip so the lineage stays visible across states.

        ``target`` is "result" (its home, under the result prompt and above the
        Generate row) or "generating" (under the progress bar, so the user keeps
        seeing the versions while the next edit renders). Moving between layouts
        reparents the single strip instance - it is never rebuilt, so tiles and
        selection survive the move.
        """
        self._main_layout.removeWidget(self._version_strip)
        self._result_prompt_layout.removeWidget(self._version_strip)
        if target == "generating":
            # Sit between the prompt and the progress bar (above it), not below.
            idx = self._main_layout.indexOf(self._progress_widget)
            self._main_layout.insertWidget(idx, self._version_strip)
        else:
            # Index 1 = right after the result prompt container (index 0).
            self._result_prompt_layout.insertWidget(1, self._version_strip)
        self._version_strip.setVisible(self._version_strip.count() > 0)

    def _sync_attach_buttons(self) -> None:
        """Mirror the reference count onto both prompt containers, and hide
        the Reference chip when the server switches references off.

        Capacity no longer hides the chip (2026-08-03): it opens the
        Reference panel now, a management surface where a full store is
        exactly when the user wants in (edit notes, remove one). The
        capacity ceiling itself is enforced at add time by the store."""
        if self._reference_widget is None:
            return
        from ...core.auth.activation_manager import is_feature_enabled

        enabled = is_feature_enabled("references")
        count = self._reference_widget.count()
        if count > 0:
            self._mark_guide_ai_touched()
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_attach_enabled(enabled)
            container.set_reference_count(count)

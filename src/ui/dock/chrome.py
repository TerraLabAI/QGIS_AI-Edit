from __future__ import annotations

import os

from qgis.PyQt.QtCore import QTimer
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
    _BTN_GREEN_AUTH,
    _BTN_PAIR_CANCEL,
    _BTN_PAIR_NEUTRAL,
    BRAND_BLUE,
    TERRALAB_URL,
)
from .widgets import _Spinner


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
            f'<a href="{TERRALAB_URL}" '
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
        # "5 free AI Edits" hint were tried here and rejected: text only.
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
        for line in (
            tr("Free account - sign up takes 15 seconds in your browser."),
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
            " text-decoration: underline; }"
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

        # CTA button displayed on activation flow when usage limit is reached
        self._activation_limit_cta_btn = QPushButton(tr("Subscribe"))
        self._activation_limit_cta_btn.setToolTip(tr("Open the subscription page in your browser"))
        self._activation_limit_cta_btn.setCursor(QtC.PointingHandCursor)
        self._activation_limit_cta_btn.setStyleSheet(_BTN_BLUE_AUTH)
        self._activation_limit_cta_btn.clicked.connect(self._on_activation_limit_cta_clicked)
        self._activation_limit_cta_btn.setVisible(False)
        layout.addWidget(self._activation_limit_cta_btn)
        self._activation_limit_cta_url = ""

        return widget

    def _set_upgrade_cta_wanted(self, wanted: bool) -> None:
        self._upgrade_cta_wanted = wanted
        self._upgrade_cta.setVisible(wanted)
        self._apply_footer_responsive()

    def _set_credits_wanted(self, wanted: bool) -> None:
        self._credits_wanted = wanted
        self._apply_footer_responsive()

    def _set_upgrade_cta_text(self, full: bool) -> None:
        """Full label vs the short "More detail" fallback. Guarded so
        resizeEvent (which fires often) only relayouts when the text changes."""
        text = tr("Unlock more detail") if full else tr("More detail")
        if self._upgrade_cta.text() != text:
            self._upgrade_cta.setText(text)

    def _apply_footer_responsive(self) -> None:
        """Collapse low-priority footer items, by priority, until the row fits
        the dock width. Measured (not threshold-based) so it stays correct
        across font size, DPI and translated pill length.

        The right-side icons (vectorize / swipe / settings / help) are never
        touched - they always stay reachable. Kept longest -> dropped first:
          1. usage count label ("100 / 200")
          2. upgrade pill text shortened to "Upgrade" (the CTA stays visible)
          3. credit ring (last resort only)
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
        self._set_upgrade_cta_text(full=True)

        def fits() -> bool:
            # invalidate() drops the layout's cached hint so the measurement
            # reflects the visibility / text changes made just above.
            self._footer_row.invalidate()
            return self._footer_row.sizeHint().width() <= avail

        if not fits() and self._credits_wanted:
            self._credits_label.setVisible(False)
        if not fits() and self._upgrade_cta_wanted:
            self._set_upgrade_cta_text(full=False)
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
        # Container only exists to right-align the badge.
        self._update_notif_container = QWidget()
        container_layout = QHBoxLayout(self._update_notif_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addStretch()

        self._update_notification_label = QLabel("")
        self._update_notification_label.setStyleSheet(
            "background-color: rgba(25, 118, 210, 0.15); "
            "border: 2px solid rgba(25, 118, 210, 0.4); border-radius: 6px; "
            "padding: 6px 12px; font-size: 12px; font-weight: bold; color: palette(text);"
        )
        self._update_notification_label.setOpenExternalLinks(False)
        self._update_notification_label.linkActivated.connect(self._on_open_plugin_manager)
        container_layout.addWidget(self._update_notification_label)

        self._update_notif_container.setVisible(False)
        parent_layout.addWidget(self._update_notif_container)

    def check_for_updates(self) -> bool:
        """Show the update banner if QGIS reports a newer plugin version.

        Reads QGIS's cached plugin-repository metadata (the plugin itself makes
        no network call). Returns True once a newer version is detected so the
        caller can stop polling.
        """
        try:
            from pyplugin_installer.installer_data import plugins

            # The pyplugin_installer key is the installed plugin's folder name,
            # which equals this package's root directory name. In a dev install
            # the folder name differs from the published id, so no banner shows.
            plugin_id = os.path.basename(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            )
            plugin_data = plugins.all().get(plugin_id)
            if plugin_data and plugin_data.get("status") == "upgradeable":
                available_version = plugin_data.get("version_available", "?")
                text = '{} <a href="#update" style="color: #1976d2; font-weight: bold;">{}</a>'.format(
                    tr("New version available: v{version}").format(version=available_version),
                    tr("Update now"),
                )
                self._update_notification_label.setText(text)
                self._update_notif_container.setVisible(True)
                return True
        except Exception:
            pass  # nosec B110  No repo metadata yet, dev install, etc.
        return False

    def _on_open_plugin_manager(self, _link: str = "") -> None:
        """Open QGIS's Plugin Manager on the Upgradeable tab (index 3)."""
        try:
            from qgis.utils import iface

            iface.pluginManagerInterface().showPluginManager(3)
        except Exception:
            pass  # nosec B110

    def _build_warning_widget(self) -> QWidget:
        """Build the empty-canvas first-run hero (shown when no visible layer).

        The empty state IS the onboarding. It leads with the truth the user
        must act on - the imagery is THEIRS to bring (any GeoTIFF / WMS / XYZ) -
        and keeps a one-click "Try it on an example" demo as the reassurance
        fallback for someone with no data on hand (Yvann 2026-07-08). It mirrors
        the AI Segmentation hero pixel for pixel so the two docks read as one
        family. No illustrative preview image: real product output only, a
        glyph is fine. The plugin handles the demo: adds a basemap, frames the
        scene, pre-draws a zone and pre-fills the most-used preset.

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

        title = QLabel(tr("Load your own imagery"))
        title.setWordWrap(True)
        title.setAlignment(QtC.AlignCenter)
        title.setStyleSheet("font-weight: 700; font-size: 15px;")
        col.addWidget(title)

        # One quiet line, one job: name what counts as imagery. No workflow
        # prose. Kept as _warning_text so show_basemap_error can swap it.
        self._warning_text = QLabel(tr("Any GeoTIFF, WMS or XYZ basemap."))
        self._warning_text.setWordWrap(True)
        self._warning_text.setAlignment(QtC.AlignCenter)
        self._warning_text.setStyleSheet(
            "font-size: 11px; color: rgba(128, 128, 128, 0.95);")
        col.addWidget(self._warning_text)

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

        self._basemap_btn = QPushButton(tr("Try it on an example"))
        self._basemap_btn.setCursor(QtC.PointingHandCursor)
        self._basemap_btn.setMinimumHeight(30)
        self._basemap_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._basemap_btn.clicked.connect(self._on_try_example_clicked)
        col.addWidget(self._basemap_btn)

        outer.addWidget(card)
        outer.addStretch(1)
        return wrapper

    def _on_try_example_clicked(self):
        """One-click unblock for the empty-canvas gate. The heavy lifting (add a
        basemap, frame a demo scene, pre-draw a zone, pre-fill a prompt) lives
        in the plugin, which owns the map tool, canvas and template state. The
        dock only asks for it; it stays a pure state machine."""
        self.try_example_requested.emit()

    def show_basemap_error(self):
        """Surface a load failure in the warning box (called by the plugin when
        neither the demo nor the fallback basemap could be added)."""
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
        """Hide the + button at capacity, and mirror the reference count onto
        both prompt containers so the Ref image control shows how many images
        are attached (the link to the thumbnails above)."""
        if self._reference_widget is None:
            return
        enabled = not self._reference_widget.at_capacity()
        count = self._reference_widget.count()
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_attach_enabled(enabled)
            container.set_reference_count(count)
